"""D5 管线衔接：爬取原始数据 → V1.0 schema 12列CSV。

字段映射与降级策略：
- title          <- 列表页标题（已是干净文本）
- company_name   <- extract.supplier（法人实体，交给阶段1实体对齐）
- amount         <- extract.amount_normalized（已是元）
- announcement_date <- 列表页 publish_time 日期部分
- province       <- 行政区域归一化（"浙江省"→"浙江"，"广西壮族自治区"→"广西"）
- city           <- 空（列表页行政区域只到省级）
- purchaser      <- 正文提取，回退列表页自带（"详情见公告正文"视为无效）
- project_id     <- extract.project_id
- industry       <- 规则粗分类（爬虫数据无人工标注；按标题/项目名关键词，兜底"其他"）
- tech_tags      <- 空（V2.0 不自动打标，避免噪声）
- summary        <- 正文截断前200字
- source_url     <- url

去重强化（方案5.3）：
- project_id 归一化（大写/去空白）后精确去重
- 标题编辑距离比 >0.92 视为同一公告（跨栏目重复收录）
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from loguru import logger

from src.crawler.extract import FieldExtractor
from src.crawler.storage import RawStore

OUT_CSV = Path("data/processed/bidding_crawled.csv")
EXTRACT_STATS = Path("data/processed/extract_stats.json")  # D7 质量仪表盘数据源

# V1.0 schema 12列（与 data/raw/bidding_sample.csv 一致）
COLUMNS = [
    "title", "company_name", "amount", "announcement_date",
    "province", "city", "purchaser", "project_id",
    "industry", "tech_tags", "summary", "source_url",
]

# 行业规则粗分类：关键词 → 行业（按顺序首个命中）。爬虫数据无人工标注，宁粗勿错
INDUSTRY_RULES: list[tuple[str, list[str]]] = [
    ("智慧教育", ["学校", "幼儿园", "教学", "教育", "校园", "培训", "实训"]),
    ("智能交通", ["交通", "道路", "信号灯", "公路", "车辆", "停车", "运输"]),
    ("智慧医疗", ["医院", "医疗", "卫生", "诊疗", "医用", "护理", "康复", "保健"]),
    ("智慧城市", ["智慧城市", "城市大脑", "数字化城市", "市政"]),
    ("政务监管", ["监管", "政务", "执法", "公安", "法庭", "检察"]),
    ("乡村振兴", ["乡村", "农村", "农业", "耕地", "农田", "养殖", "种植"]),
    ("数据中心", ["数据中心", "机房"]),
    ("云计算", ["云平台", "云计算", "云服务"]),
    ("物业服务", ["物业"]),
]

# 省级行政区后缀（归一化用）
_PROVINCE_SUFFIXES = (
    "维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "省", "市", "特别行政区",
)


def normalize_province(region: str) -> str:
    """行政区域 → 省份简称（"浙江省"→"浙江"，"广西壮族自治区"→"广西"）。"""
    region = (region or "").strip()
    if not region:
        return ""
    for suffix in _PROVINCE_SUFFIXES:
        if region.endswith(suffix) and len(region) > len(suffix):
            region = region[: -len(suffix)]
            break
    return region


# 日期双格式（IT-021）：列表页元数据为 "2026-09-17 17:22"，正文公告时间为
# "2026年09月17日  09:16"——手工离线导入无列表页元数据，必须识别年月日格式
_DATE_ISO_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_DATE_CN_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def extract_date(text: str) -> str:
    """从日期文本提取 ISO 日期（YYYY-MM-DD），支持横杠与年月日双格式。"""
    text = text or ""
    m = _DATE_ISO_RE.search(text)
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        return f"{y}-{mo:02d}-{d:02d}"
    m = _DATE_CN_RE.search(text)
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        return f"{y}-{mo:02d}-{d:02d}"
    return ""


def classify_industry(title: str, project_name: str = "") -> str:
    """规则粗分类：标题+项目名关键词匹配，兜底"其他"。"""
    text = f"{title} {project_name}"
    for industry, keywords in INDUSTRY_RULES:
        if any(kw in text for kw in keywords):
            return industry
    return "其他"


def is_duplicate_title(title: str, seen_titles: list[str], threshold: float = 0.92) -> bool:
    """标题编辑距离比超过阈值视为同一公告（跨栏目重复收录）。"""
    for seen in seen_titles:
        if SequenceMatcher(None, title, seen).ratio() > threshold:
            return True
    return False


def convert(raw_records: list[dict]) -> pd.DataFrame:
    """原始公告列表 → V1.0 schema DataFrame（含提取、映射、去重）。"""
    extractor = FieldExtractor()
    rows: list[dict] = []
    seen_pids: set[str] = set()
    seen_titles: list[str] = []

    for d in raw_records:
        r = extractor.extract(d["body_text"])

        company = r.fields.get("supplier", "")
        amount = r.amount_normalized
        project_id = r.fields.get("project_id", "")
        title = d.get("title", "")

        # ---- 去重（方案5.3）------------------------------------------
        pid_norm = project_id.upper().replace(" ", "")
        if pid_norm and pid_norm in seen_pids:
            logger.debug("去重(project_id): {}", title[:30])
            continue
        if is_duplicate_title(title, seen_titles):
            logger.debug("去重(标题相似): {}", title[:30])
            continue
        if pid_norm:
            seen_pids.add(pid_norm)
        seen_titles.append(title)

        # ---- 字段映射 -------------------------------------------------
        date_full = d.get("publish_time", "") or r.fields.get("announce_time", "")
        date = extract_date(date_full)

        purchaser = r.fields.get("purchaser", "")
        if not purchaser or purchaser in ("详情见公告正文",):
            purchaser = d.get("purchaser_from_list", "")
            if purchaser in ("详情见公告正文",):
                purchaser = ""

        province = normalize_province(r.fields.get("region", "") or d.get("region", ""))

        rows.append(
            {
                "title": title,
                "company_name": company,
                # 金额为 0/空（分标按实结算类公告无总价）→ 留空由阶段1剔除（宁缺勿错）
                "amount": amount if amount else "",
                "announcement_date": date,
                "province": province,
                # 爬虫数据的行政区域只到省级，V1.0 schema city 必填 → 用省名填充
                "city": province,
                "purchaser": purchaser,
                "project_id": project_id,
                "industry": classify_industry(title, r.fields.get("project_name", "")),
                "tech_tags": "",
                "summary": (r.fields.get("project_name") or d["body_text"][:200]),
                "source_url": d.get("url", ""),
                "_needs_review": [f for f in ("supplier", "amount") if f in r.missing],
            }
        )

    df = pd.DataFrame(rows, columns=COLUMNS + ["_needs_review"])
    return df


def main() -> None:
    store = RawStore()
    records = [vars(a) if hasattr(a, "__dict__") else a.__dict__ for a in store.load_all()]
    if not records:
        logger.error("无原始数据，请先运行: ./run.sh scripts/crawl_ccgp.py")
        sys.exit(1)

    logger.info("原始公告 {} 条，开始解析映射…", len(records))
    df = convert(records)

    # ---- 提取质量统计（D7 仪表盘数据源：策略来源分布/缺失分布/行业分布）----
    extractor = FieldExtractor()
    sources: Counter = Counter()
    missing_fields: Counter = Counter()
    industries: Counter = Counter()
    for d in records:
        r = extractor.extract(d["body_text"])
        sources.update(r.sources.values())
        missing_fields.update(r.missing)
    industries.update(df["industry"])

    stats = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "total_raw": len(records),
        "total_deduped": len(df),
        "field_sources": dict(sources),
        "missing_fields": dict(missing_fields),
        "industry_distribution": dict(industries),
    }
    EXTRACT_STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("提取质量统计 -> {}", EXTRACT_STATS)

    # 数据质量统计
    n = len(df)
    stats = {
        "供应商": df["company_name"].ne("").sum(),
        "金额": df["amount"].ne("").sum(),
        "日期": df["announcement_date"].ne("").sum(),
        "采购人": df["purchaser"].ne("").sum(),
        "编号": df["project_id"].ne("").sum(),
    }
    logger.info("转换完成: {} 条（去重删除 {} 条）", n, len(records) - n)
    for k, v in stats.items():
        logger.info("  {}: {}/{} ({:.0f}%)", k, v, n, v / n * 100)
    logger.info("行业分布:\n{}", df["industry"].value_counts().to_string())
    review = df[df["_needs_review"].astype(bool)]
    logger.info("needs_review: {} 条", len(review))

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8")
    logger.info("已输出 -> {}", OUT_CSV)


if __name__ == "__main__":
    main()
