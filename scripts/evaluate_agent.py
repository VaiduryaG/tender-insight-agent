"""Agent 自动化评估脚本。

用法：
    ./run.sh scripts/evaluate_agent.py

功能：
- 批量运行 20 个测试问题
- 自动检查工具调用正确性
- 自动检查答案完整性
- 幻觉检测
- 生成评估报告
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger

from src.agent.graph_agent import LangGraphAgent
from src.mcp_tools.tools import build_registry


# 测试用例定义
TEST_CASES = [
    # 类别 1：单工具调用
    {
        "id": "TC001",
        "category": "single_tool",
        "question": "中国电信在中标数据里的表现如何？",
        "expected_tools": ["company_profile"],
        "expected_keywords": ["项目", "金额", "行业"],
        "expected_entities": ["中国电信"],
    },
    {
        "id": "TC002",
        "category": "single_tool",
        "question": "智慧教育行业的项目数量和金额是多少？",
        "expected_tools": ["industry_stats"],
        "expected_keywords": ["项目", "金额", "智慧教育"],
        "expected_entities": [],
    },
    {
        "id": "TC003",
        "category": "single_tool",
        "question": "帮我找找和校园信息化相关的项目",
        "expected_tools": ["semantic_search"],
        "expected_keywords": ["校园", "信息化", "项目"],
        "expected_entities": [],
    },
    {
        "id": "TC004",
        "category": "single_tool",
        "question": "物联网技术的需求热度怎么样？",
        "expected_tools": ["graph_query"],
        "expected_keywords": ["物联网", "项目"],
        "expected_entities": [],
    },
    {
        "id": "TC005",
        "category": "single_tool",
        "question": "华为的竞争对手有哪些？",
        "expected_tools": ["graph_query"],
        "expected_keywords": ["竞争对手", "公司"],
        "expected_entities": ["华为"],
    },
    # 类别 2：链式工具调用
    {
        "id": "TC006",
        "category": "chain_tool",
        "question": "智慧教育行业金额最高的公司是哪家？它有哪些竞争对手？",
        "expected_tools": ["industry_stats", "graph_query"],
        "expected_keywords": ["公司", "金额", "竞争对手"],
        "expected_entities": [],
    },
    {
        "id": "TC007",
        "category": "chain_tool",
        "question": "中国联通在哪些行业有中标项目？每个行业的金额是多少？",
        "expected_tools": ["company_profile", "industry_stats"],
        "expected_keywords": ["中国联通", "行业", "金额"],
        "expected_entities": ["中国联通"],
    },
    {
        "id": "TC008",
        "category": "chain_tool",
        "question": "找找智慧医疗相关的项目，分析中标公司的分布情况",
        "expected_tools": ["semantic_search", "company_profile"],
        "expected_keywords": ["智慧医疗", "项目", "公司"],
        "expected_entities": [],
    },
    {
        "id": "TC009",
        "category": "chain_tool",
        "question": "中国电信的竞争对手有哪些？分别在中标金额上有什么差异？",
        "expected_tools": ["graph_query", "company_profile"],
        "expected_keywords": ["竞争对手", "金额"],
        "expected_entities": ["中国电信"],
    },
    {
        "id": "TC010",
        "category": "chain_tool",
        "question": "人工智能技术在哪些项目中使用？这些项目的金额分布如何？",
        "expected_tools": ["graph_query", "semantic_search"],
        "expected_keywords": ["人工智能", "项目", "金额"],
        "expected_entities": [],
    },
    # 类别 3：边缘案例
    {
        "id": "TC011",
        "category": "edge_case",
        "question": "阿里巴巴在中标数据里的表现如何？",
        "expected_tools": ["company_profile"],
        "expected_keywords": [],
        "expected_entities": [],
        "expect_not_found": True,
    },
    {
        "id": "TC012",
        "category": "edge_case",
        "question": "电信",
        "expected_tools": ["company_profile", "graph_query"],
        "expected_keywords": ["电信"],
        "expected_entities": [],
    },
    {
        "id": "TC013",
        "category": "edge_case",
        "question": "航天技术在哪些项目中使用？",
        "expected_tools": ["graph_query", "semantic_search"],
        "expected_keywords": [],
        "expected_entities": [],
        "allow_empty": True,
    },
    {
        "id": "TC014",
        "category": "edge_case",
        "question": "在智慧医疗行业中，金额超过 100 万的项目有哪些？",
        "expected_tools": ["search_projects", "industry_stats"],
        "expected_keywords": ["智慧医疗", "金额", "项目"],
        "expected_entities": [],
    },
    {
        "id": "TC015",
        "category": "edge_case",
        "question": "2026 年 9 月的中标项目有哪些？",
        "expected_tools": ["industry_stats", "search_projects"],
        "expected_keywords": ["2026", "项目"],
        "expected_entities": [],
    },
    # 类别 4：幻觉检测
    {
        "id": "TC016",
        "category": "hallucination",
        "question": "XX公司（不存在的公司）的中标金额是多少？",
        "expected_tools": ["company_profile"],
        "expected_keywords": [],
        "expected_entities": [],
        "expect_not_found": True,
        "no_fake_data": True,
    },
    {
        "id": "TC017",
        "category": "hallucination",
        "question": "虚拟现实行业有多少个中标项目？",
        "expected_tools": ["industry_stats", "graph_query"],
        "expected_keywords": [],
        "expected_entities": [],
        "allow_empty": True,
        "no_fake_data": True,
    },
    {
        "id": "TC018",
        "category": "hallucination",
        "question": "智慧城市行业金额最高的公司是哪家？",
        "expected_tools": ["industry_stats"],
        "expected_keywords": ["公司", "金额", "智慧城市"],
        "expected_entities": [],
        "no_simulation": True,
    },
    {
        "id": "TC019",
        "category": "hallucination",
        "question": "列出所有中标金额超过 1000 万的公司",
        "expected_tools": ["search_projects", "industry_stats"],
        "expected_keywords": ["金额", "公司"],
        "expected_entities": [],
        "no_fake_data": True,
    },
    {
        "id": "TC020",
        "category": "hallucination",
        "question": "中国电信的总中标金额是多少？再问一次。",
        "expected_tools": ["company_profile"],
        "expected_keywords": ["金额", "中国电信"],
        "expected_entities": ["中国电信"],
        "check_consistency": True,
    },
]


def extract_tool_calls(trace: list[dict]) -> list[str]:
    """从 trace 中提取工具调用列表。"""
    tools = []
    for step in trace:
        if step.get("tool_calls"):
            for tc in step["tool_calls"]:
                tools.append(tc["name"])
    return tools


def check_keywords(answer: str, keywords: list[str]) -> float:
    """检查答案是否包含关键词，返回得分（0-1）。"""
    if not keywords:
        return 1.0

    answer_lower = answer.lower()
    matched = sum(1 for kw in keywords if kw.lower() in answer_lower)
    return matched / len(keywords)


def check_entities(answer: str, entities: list[str]) -> bool:
    """检查答案是否包含预期实体。"""
    if not entities:
        return True

    answer_lower = answer.lower()
    return all(ent.lower() in answer_lower for ent in entities)


def detect_hallucination(answer: str, tc: dict) -> tuple[bool, str]:
    """检测幻觉（编造信息）。"""
    # 检测占位符（如"XX公司"、"YY公司"）
    placeholders = ["XX公司", "YY公司", "ZZ公司", "某公司", "某行业"]
    for ph in placeholders:
        if ph in answer:
            return True, f"检测到占位符: {ph}"

    # 检测"不存在"场景下是否有虚假数据
    if tc.get("expect_not_found"):
        # 如果预期"未找到"，但答案包含具体数字，可能是编造
        import re

        amounts = re.findall(r"\d+\.?\d*万?元", answer)
        if amounts and "未找到" not in answer and "不存在" not in answer:
            return True, "预期未找到，但答案包含具体金额数据"

    # 检测"不编造"要求
    if tc.get("no_fake_data"):
        # 检查是否有"XX公司"之类的占位符
        if any(ph in answer for ph in placeholders):
            return True, "答案包含占位符，可能编造了数据"

    return False, ""


def evaluate_single_case(agent: LangGraphAgent, tc: dict) -> dict[str, Any]:
    """评估单个测试用例。"""
    result = {
        "id": tc["id"],
        "category": tc["category"],
        "question": tc["question"],
        "passed": False,
        "tool_correctness": 0.0,
        "answer_completeness": 0.0,
        "hallucination": False,
        "hallucination_reason": "",
        "tools_called": [],
        "answer": "",
        "details": {},
    }

    try:
        # 运行 Agent
        agent_result = agent.run(tc["question"])
        answer = agent_result.answer or ""
        trace = agent_result.trace

        result["answer"] = answer[:200]  # 只保存前 200 字符
        result["tools_called"] = extract_tool_calls(trace)

        # 评估工具调用正确性
        expected_tools = tc.get("expected_tools", [])
        called_tools = result["tools_called"]

        if expected_tools:
            # 计算工具调用正确率
            matched = sum(1 for t in expected_tools if any(t in ct for ct in called_tools))
            result["tool_correctness"] = matched / len(expected_tools) if expected_tools else 1.0
        else:
            result["tool_correctness"] = 1.0

        # 评估答案完整性
        keywords = tc.get("expected_keywords", [])
        result["answer_completeness"] = check_keywords(answer, keywords)

        # 检查实体
        entities = tc.get("expected_entities", [])
        entity_match = check_entities(answer, entities)
        if entities and not entity_match:
            result["answer_completeness"] *= 0.5  # 实体不匹配扣分

        # 幻觉检测
        is_hallucination, reason = detect_hallucination(answer, tc)
        result["hallucination"] = is_hallucination
        result["hallucination_reason"] = reason

        # 综合判断是否通过
        result["passed"] = (
            result["tool_correctness"] >= 0.5
            and result["answer_completeness"] >= 0.5
            and not result["hallucination"]
        )

    except Exception as e:
        result["error"] = str(e)
        result["passed"] = False

    return result


def generate_report(results: list[dict]) -> str:
    """生成评估报告。"""
    total = len(results)
    passed = sum(1 for r in results if r["passed"])

    # 按类别统计
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "passed": 0, "tool_score": [], "answer_score": []}
        categories[cat]["total"] += 1
        categories[cat]["passed"] += 1 if r["passed"] else 0
        categories[cat]["tool_score"].append(r["tool_correctness"])
        categories[cat]["answer_score"].append(r["answer_completeness"])

    # 生成报告
    report = f"""# Agent 评估报告

## 总体概况

- **测试用例总数**: {total}
- **通过数量**: {passed}
- **通过率**: {passed / total * 100:.1f}%
- **平均工具调用正确性**: {sum(r['tool_correctness'] for r in results) / total * 100:.1f}%
- **平均答案完整性**: {sum(r['answer_completeness'] for r in results) / total * 100:.1f}%
- **幻觉检测**: {sum(1 for r in results if r['hallucination'])} 个问题存在幻觉

---

## 按类别统计

"""

    for cat, stats in categories.items():
        cat_pass_rate = stats["passed"] / stats["total"] * 100
        avg_tool = sum(stats["tool_score"]) / len(stats["tool_score"]) * 100
        avg_answer = sum(stats["answer_score"]) / len(stats["answer_score"]) * 100

        report += f"""### {cat}

- **通过率**: {stats['passed']}/{stats['total']} ({cat_pass_rate:.1f}%)
- **工具调用正确性**: {avg_tool:.1f}%
- **答案完整性**: {avg_answer:.1f}%

"""

    report += """---

## 详细结果

"""

    for r in results:
        status = "✅ 通过" if r["passed"] else "❌ 失败"
        report += f"""### {r['id']} {status}

**问题**: {r['question']}

**调用工具**: {', '.join(r['tools_called']) if r['tools_called'] else '无'}

**工具正确性**: {r['tool_correctness'] * 100:.1f}%

**答案完整性**: {r['answer_completeness'] * 100:.1f}%

**答案摘要**: {r['answer'][:100]}...

"""
        if r["hallucination"]:
            report += f"""**⚠️ 幻觉检测**: {r['hallucination_reason']}

"""

        report += "\n"

    return report


def main() -> None:
    """运行评估。"""
    logger.info("开始 Agent 评估...")

    # 初始化 Agent
    registry = build_registry()
    agent = LangGraphAgent(registry)

    # 运行评估
    results = []
    for i, tc in enumerate(TEST_CASES, 1):
        logger.info("评估 [{}/{}]: {}", i, len(TEST_CASES), tc["id"])
        result = evaluate_single_case(agent, tc)
        results.append(result)

        status = "✅" if result["passed"] else "❌"
        logger.info("  {} 工具: {:.1f}% 答案: {:.1f}%", status, result["tool_correctness"] * 100, result["answer_completeness"] * 100)

    # 生成报告
    report = generate_report(results)

    # 保存报告
    report_path = Path("data/evaluation_report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    # 保存详细结果
    results_path = Path("data/evaluation_results.json")
    results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 输出摘要
    passed = sum(1 for r in results if r["passed"])
    logger.success(
        "评估完成: {}/{} 通过 ({:.1f}%)",
        passed,
        len(results),
        passed / len(results) * 100,
    )
    logger.info("报告已保存: {}", report_path)


if __name__ == "__main__":
    main()