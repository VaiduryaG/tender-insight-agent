"""LLM 辅助行业分类：对"其他"/"未分类"项目进行智能分类。

用法：
    ./run.sh scripts/classify_industry.py              # 分类所有"其他"项目
    ./run.sh scripts/classify_industry.py --dry-run    # 预览分类结果但不写入

策略：
- 输入：项目标题+摘要+技术标签
- 输出：行业分类（从已知行业列表中选择）+ 置信度 + 推理过程
- 保守原则：置信度 < 0.8 或 LLM 选择"其他"时保持原样
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.llm import LLMClient
from src.data_processing.cleaner import OPTIONAL_DEFAULTS

# 已知行业列表（从现有数据提取）
KNOWN_INDUSTRIES = [
    "智慧教育",
    "智慧医疗",
    "智能交通",
    "乡村振兴",
    "政务监管",
    "物业服务",
    "医疗卫生",
    "智慧城市",
    "教育后勤",
    "其他",
]

CLASSIFY_PROMPT = """你是一个政府采购公告行业分类专家。根据项目信息判断其所属行业。

已知行业列表：
{industries}

项目信息：
标题：{title}
摘要：{summary}
技术标签：{tech_tags}

请判断该项目属于哪个行业。如果无法判断或不在已知行业中，选择"其他"。

请以 JSON 格式返回：
{{
    "industry": "行业名称",
    "confidence": 0.0-1.0的置信度,
    "reasoning": "分类理由（一句话）"
}}

只返回 JSON，不要其他内容。"""


def classify_item(
    client: LLMClient,
    title: str,
    summary: str,
    tech_tags: str,
    industries: list[str] = KNOWN_INDUSTRIES,
) -> dict:
    """调用 LLM 对单个项目进行行业分类。"""
    prompt = CLASSIFY_PROMPT.format(
        industries="、".join(industries),
        title=title[:100],  # 截断过长标题
        summary=summary[:200] if summary else "无",
        tech_tags=tech_tags if tech_tags else "无",
    )

    try:
        resp = client.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,  # 低温度保证一致性
            max_tokens=200,
        )
        content = resp["content"]
        if not content:
            return {"industry": "其他", "confidence": 0.0, "reasoning": "LLM 未返回内容"}

        # 解析 JSON
        # LLM 可能返回 ```json ... ``` 格式，需要提取
        if "```" in content:
            # 提取 ```json ... ``` 中的内容
            import re
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
            if match:
                content = match.group(1).strip()

        result = json.loads(content)
        # 校验行业是否在已知列表中
        if result.get("industry") not in industries:
            result["industry"] = "其他"
            result["reasoning"] = f"LLM 返回的行业'{result.get('industry')}'不在已知列表中"

        return result

    except json.JSONDecodeError as e:
        logger.warning("JSON 解析失败: {} - {}", str(e)[:50], content[:100])
        return {"industry": "其他", "confidence": 0.0, "reasoning": f"JSON解析失败: {str(e)[:30]}"}
    except Exception as e:
        logger.warning("分类失败: {}", str(e)[:80])
        return {"industry": "其他", "confidence": 0.0, "reasoning": f"分类异常: {str(e)[:30]}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 辅助行业分类")
    parser.add_argument("--dry-run", action="store_true", help="预览分类结果但不写入")
    args = parser.parse_args()

    # 读取处理后的数据
    input_path = Path("data/processed/bidding_stage1.parquet")
    if not input_path.exists():
        logger.error("数据文件不存在: {}", input_path)
        sys.exit(1)

    df = pd.read_parquet(input_path)
    logger.info("总数据量: {}", len(df))

    # 筛选需要分类的项目
    to_classify = df[df["industry"].isin(["其他", "未分类", OPTIONAL_DEFAULTS["industry"]])]
    logger.info("需要分类的项目: {}", len(to_classify))

    if len(to_classify) == 0:
        logger.info("所有项目已分类，无需处理")
        return

    # 初始化 LLM 客户端
    client = LLMClient()

    # 分类
    results = []
    for idx, row in to_classify.iterrows():
        logger.info("正在分类 [{}/{}]: {}", len(results) + 1, len(to_classify), row["title"][:40])

        result = classify_item(
            client=client,
            title=row["title"],
            summary=row.get("summary", ""),
            tech_tags=row.get("tech_tags", ""),
        )

        result["original_industry"] = row["industry"]
        result["title"] = row["title"]
        result["confidence"] = float(result.get("confidence", 0.0))
        results.append(result)

        # 只有置信度 >= 0.8 且不是"其他"才记录为成功分类
        if result["confidence"] >= 0.8 and result["industry"] != "其他":
            logger.success(
                "  ✓ 分类为 {} (置信度 {:.2f}): {}",
                result["industry"],
                result["confidence"],
                result["reasoning"],
            )
        else:
            logger.info(
                "  - 保持原样 (置信度 {:.2f}): {}",
                result["confidence"],
                result["reasoning"][:50],
            )

    # 统计
    reclassified = [r for r in results if r["confidence"] >= 0.8 and r["industry"] != "其他"]
    logger.info("\n分类完成：共 {} 项，成功重新分类 {} 项", len(results), len(reclassified))

    # 分类统计
    if reclassified:
        from collections import Counter

        industry_counts = Counter(r["industry"] for r in reclassified)
        logger.info("\n新分类统计：")
        for industry, count in industry_counts.most_common():
            logger.info("  {}: {} 项", industry, count)

    if args.dry_run:
        logger.info("\n[DRY RUN] 预览模式，不写入文件")
        # 输出详细分类结果
        print("\n详细分类结果：")
        for r in results[:10]:  # 只显示前10条
            print(f"\n标题: {r['title'][:50]}")
            print(f"原分类: {r['original_industry']}")
            print(f"新分类: {r['industry']} (置信度: {r['confidence']:.2f})")
            print(f"理由: {r['reasoning']}")
        return

    # 写回数据
    if reclassified:
        # 创建新列 industry_llm
        df["industry_llm"] = df["industry"]
        for r in reclassified:
            # 找到对应行并更新
            mask = df["title"] == r["title"]
            df.loc[mask, "industry_llm"] = r["industry"]

        # 保存
        output_path = input_path
        df.to_parquet(output_path, index=False)
        logger.success("数据已更新: {}", output_path)

        # 更新后的统计
        new_other_count = (df["industry_llm"] == "其他").sum()
        old_other_count = (df["industry"] == "其他").sum()
        logger.info(
            "\"其他\"占比: {:.1f}% → {:.1f}%",
            old_other_count / len(df) * 100,
            new_other_count / len(df) * 100,
        )

        # 生成分类报告
        report_path = Path("data/processed/industry_classification_report.json")
        report = {
            "total_classified": len(results),
            "successful_reclassification": len(reclassified),
            "old_other_ratio": old_other_count / len(df),
            "new_other_ratio": new_other_count / len(df),
            "details": reclassified,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.success("分类报告已保存: {}", report_path)


if __name__ == "__main__":
    main()