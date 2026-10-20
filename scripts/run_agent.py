"""Agent 总入口：构建 LangGraph Agent，运行演示问题。

用法:
    ./run.sh scripts/run_agent.py                # 跑 3 个演示问题
    ./run.sh scripts/run_agent.py --full         # 跑全部 10 个验收问题
    ./run.sh scripts/run_agent.py -q "你的问题"   # 单问
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger

from src.agent.conversation import Conversation
from src.agent.graph_agent import LangGraphAgent
from src.agent.llm import LLMClient
from src.mcp_tools.tools import build_registry

DEMO_QUESTIONS = [
    "中国电信在中标数据里的表现如何？",
    "智慧教育行业谁的金额最高？它有哪些竞争对手？",
    "帮我找找和校园信息化建设相关的项目",
]

FULL_QUESTIONS = [
    *DEMO_QUESTIONS,
    "目前数据里哪个行业的中标总金额最大？",
    "山东省一共有多少中标项目，总金额多少？",
    "物联网技术的需求热度怎么样？",
    "内蒙古互联盛世商务有限公司中标了什么项目？",
    "哪个集团的项目数量最多？",
    "金额在100万到200万之间的项目有哪些？",
    "中国联通的竞争对手有哪些？分别在哪些领域竞争？",
]


def build_agent(verbose: bool = True) -> LangGraphAgent:
    llm = LLMClient()
    registry = build_registry()
    from src.agent.query_rewrite import rewrite_query

    return LangGraphAgent(
        llm,
        registry,
        max_steps=8,
        verbose=verbose,
        query_rewriter=lambda q: rewrite_query(llm, q),  # RAG 查询改写
    )


def ask(agent: LangGraphAgent, question: str, history: Conversation) -> None:
    print(f"\n{'=' * 60}\n[问题] {question}\n{'-' * 60}")
    result = agent.run(question, history)
    print(result.trace_summary())
    print(f"{'-' * 60}\n[回答] {result.answer}")
    history.add_exchange(question, result.answer)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="运行全部10个验收问题")
    parser.add_argument("-q", "--question", type=str, help="单个问题")
    args = parser.parse_args()

    questions = FULL_QUESTIONS if args.full else DEMO_QUESTIONS
    if args.question:
        questions = [args.question]

    agent = build_agent()
    history = Conversation(max_messages=20)
    for q in questions:
        try:
            ask(agent, q, history)
        except Exception:
            import traceback

            logger.error("问题失败: {}", q)
            traceback.print_exc()


if __name__ == "__main__":
    main()
