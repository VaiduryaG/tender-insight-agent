"""阶段1总入口：读取原始 CSV → 清洗 → 标准化 → 实体对齐 → 输出加工数据与报告。

产物：
    data/processed/bidding_stage1.parquet   标准化数据（阶段2图谱构建的输入）
    data/processed/entity_mapping.csv       公司名映射表（人工复核 + 增量沉淀）
    data/processed/stage1_report.txt        数据质量报告
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from loguru import logger

from src.data_processing.cleaner import DataCleaner
from src.data_processing.entity_aligner import EntityAligner
from src.data_processing.normalizer import normalize_dataframe

RAW_CSV = Path("data/raw/bidding_sample.csv")
CRAWLED_CSV = Path("data/processed/bidding_crawled.csv")  # V2.0 爬虫数据（parse_raw.py 产出）
OUT_DIR = Path("data/processed")
MAPPING_CSV = Path("data/raw/company_mapping_template.csv")
EXTRACT_STATS = Path("data/processed/extract_stats.json")  # D7 爬虫提取质量统计


def load_sources() -> pd.DataFrame:
    """合并种子数据与爬虫数据（V2.0）。爬虫源可选——不存在时仅用种子。"""
    frames = [pd.read_csv(RAW_CSV)]
    if CRAWLED_CSV.exists():
        crawled = pd.read_csv(CRAWLED_CSV)
        if "_needs_review" in crawled.columns:
            crawled = crawled.drop(columns=["_needs_review"])
        logger.info("并入爬虫数据 {} 条", len(crawled))
        frames.append(crawled)
    return pd.concat(frames, ignore_index=True)


def run() -> pd.DataFrame:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("阶段1开始：读取数据源 {}{}", RAW_CSV,
                f" + {CRAWLED_CSV.name}" if CRAWLED_CSV.exists() else "")
    raw = load_sources()

    # 1) 结构性清洗
    cleaned, report = DataCleaner().clean(raw)
    logger.info("\n清洗统计:\n{}", report.summary())

    # 2) 字段标准化
    normalized = normalize_dataframe(cleaned)
    logger.info(
        "标准化完成: 金额解析成功 {}/{}, 日期解析成功 {}/{}",
        normalized["amount_normalized"].notna().sum(),
        len(normalized),
        len(normalized),
        len(normalized),
    )

    # 3) 实体对齐（V2.0：L3 LLM 复核已开启——灰区对由 GLM 确认，失败自动降级规则结果）
    aligner = EntityAligner(mapping_csv=MAPPING_CSV, use_llm=True)
    align_results = aligner.align(normalized["company_name"])
    aligned = pd.DataFrame(
        {
            "company_name": [r.original_name for r in align_results],
            "canonical_name": [r.canonical_name for r in align_results],
            "group_name": [r.group_name for r in align_results],
            "align_method": [r.method for r in align_results],
        }
    )
    normalized = normalized.merge(aligned, on="company_name", how="left")
    logger.info("实体对齐完成: {} 个公司名 -> {} 个法人实体 / {} 个集团",
                aligned["company_name"].nunique(),
                aligned["canonical_name"].nunique(),
                aligned["group_name"].nunique())

    # 3.1) needs_review 清单（低置信度对齐，供人工确认后回写映射表——D6闭环）
    review = pd.DataFrame(
        [
            {
                "original_name": r.original_name,
                "current_group": r.group_name,
                "align_method": r.method,
                "confidence_score": r.confidence,
                "is_verified": False,
                "verified_group": "",  # 人工确认后填写；与 current_group 不同则以人工为准
            }
            for r in align_results
            if r.confidence < 0.9
        ]
    )
    review_path = OUT_DIR / "needs_review.csv"
    review.to_csv(review_path, index=False, encoding="utf-8")
    logger.info("低置信度对齐 {} 条 -> {}（人工确认后回写映射表，下次L4直接命中）",
                len(review), review_path)

    # 4) 产物落盘
    parquet_path = OUT_DIR / "bidding_stage1.parquet"
    try:
        normalized.to_parquet(parquet_path, index=False)
    except Exception:
        parquet_path = OUT_DIR / "bidding_stage1.pkl"
        normalized.to_pickle(parquet_path)  # pyarrow 缺失时降级 pickle
    logger.info("标准化数据 -> {}", parquet_path)

    mapping_path = OUT_DIR / "entity_mapping.csv"
    aligner.to_mapping_dataframe(align_results).to_csv(mapping_path, index=False)
    logger.info("实体映射表 -> {}", mapping_path)

    write_report(raw, normalized, align_results, report)
    return normalized


def write_report(raw: pd.DataFrame, normalized: pd.DataFrame,
                 align_results, report) -> None:
    """生成阶段1数据质量仪表盘（D7）：含提取来源分布与历史快照对比。"""
    by_industry = normalized["industry"].value_counts().to_string()
    by_group = normalized.groupby("group_name")["amount_normalized"].sum().sort_values(ascending=False).head(10).to_string()
    groups = (
        "\n".join(f"  {r.original_name}  ->  集团: {r.group_name}"
                  for r in align_results if r.group_name != r.canonical_name)
        or "  （无跨名归组）"
    )
    method_counts = pd.Series([r.method for r in align_results]).value_counts().to_string()

    # ---- D7: 与上次快照对比（stage1_metrics.json 记录核心指标）----
    metrics_now = {
        "input": len(raw),
        "output": len(normalized),
        "entities": int(pd.Series([r.canonical_name for r in align_results]).nunique()),
        "groups": int(normalized["group_name"].nunique()),
        "amount_rate": round(float(normalized["amount_normalized"].notna().mean()), 4),
    }
    snapshot_path = OUT_DIR / "stage1_metrics.json"
    diff_lines = ["  （无历史快照，首次记录）"]
    if snapshot_path.exists():
        try:
            prev = json.loads(snapshot_path.read_text(encoding="utf-8"))
            diff_lines = [
                f"  {k}: {prev.get(k)} -> {v} ({v - prev.get(k, 0):+d})"
                if isinstance(v, int) else f"  {k}: {prev.get(k)} -> {v}"
                for k, v in metrics_now.items()
            ]
        except Exception:
            pass
    snapshot_path.write_text(json.dumps(metrics_now, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- D7: 爬虫提取质量统计（parse_raw 产出）----
    extract_section = ""
    if EXTRACT_STATS.exists():
        try:
            estats = json.loads(EXTRACT_STATS.read_text(encoding="utf-8"))
            extract_section = f"""
[爬虫提取质量]（数据源: extract_stats.json @ {estats.get('generated_at', '')}）
原始 {estats.get('total_raw')} 条，去重后 {estats.get('total_deduped')} 条
提取策略来源分布: {estats.get('field_sources')}
字段缺失: {estats.get('missing_fields')}
行业分布: {estats.get('industry_distribution')}
"""
        except Exception:
            extract_section = "\n[爬虫提取质量] extract_stats.json 解析失败\n"

    review_section = ""
    review_path = OUT_DIR / "needs_review.csv"
    if review_path.exists():
        try:
            n_review = len(pd.read_csv(review_path))
            review_section = f"\n[人工复核清单] 低置信度对齐 {n_review} 条 -> needs_review.csv\n"
        except Exception:
            pass

    text = f"""==================== 阶段1 数据质量仪表盘 ====================

[核心指标 vs 上次运行]
{chr(10).join(diff_lines)}

[清洗]
{report.summary()}

[标准化]
金额解析成功率: {normalized['amount_normalized'].notna().sum()}/{len(normalized)}
日期解析成功率: {len(normalized)}/{len(normalized)} (失败行已剔除)

[实体对齐]
公司名: {aligned_nunique(align_results)} 个 -> 法人实体 {pd.Series([r.canonical_name for r in align_results]).nunique()} 个
对齐方法分布:
{method_counts}
集团归并示例（分公司 -> 集团）:
{groups}
{review_section}{extract_section}
[数据分布]
按行业:
{by_industry}

中标金额 TOP 集团（元）:
{by_group}

============================================================
"""
    (Path("data/processed") / "stage1_report.txt").write_text(text, encoding="utf-8")
    logger.info("质量仪表盘 -> data/processed/stage1_report.txt")
    print(text)


def aligned_nunique(align_results) -> int:
    return pd.Series([r.original_name for r in align_results]).nunique()


if __name__ == "__main__":
    run()
