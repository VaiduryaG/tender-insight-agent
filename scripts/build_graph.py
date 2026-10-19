"""阶段2总入口：读取阶段1标准化数据 -> 构建图谱 -> 持久化 -> 打印统计。

产物:
    data/graph/bidding_graph.json    持久化图谱（NetworkXStore JSON 格式）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from loguru import logger

from src.knowledge_graph.builder import build_graph
from src.knowledge_graph.queries import (
    graph_overview,
    group_aggregation,
    industry_top_companies,
    technology_trends,
)
from src.knowledge_graph.store import NetworkXStore

STAGE1_DATA = Path("data/processed/bidding_stage1.parquet")
GRAPH_PATH = Path("data/graph/bidding_graph.json")


def run() -> NetworkXStore:
    if not STAGE1_DATA.exists():
        logger.error("阶段1产物不存在，请先运行: ./run.sh scripts/run_stage1.py")
        sys.exit(1)

    df = pd.read_parquet(STAGE1_DATA)
    logger.info("阶段2开始: 读取 {} ({} 条)", STAGE1_DATA, len(df))

    store = NetworkXStore()
    build_graph(df, store)

    store.save(GRAPH_PATH)
    logger.info("图谱已持久化 -> {}", GRAPH_PATH)

    print("\n" + "=" * 60)
    print(json.dumps(graph_overview(store), ensure_ascii=False, indent=2))

    print("\n---- 集团中标金额 TOP5 ----")
    for g in group_aggregation(store)[:5]:
        print(f"  {g['group']}: {g['total_amount']:,.0f} 元 ({g['project_count']} 项目)")

    print("\n---- 行业TOP公司（智慧教育）----")
    for c in industry_top_companies(store, "智慧教育"):
        print(f"  {c['company']}: {c['total_amount']:,.0f} 元")

    print("\n---- 技术需求 TOP10 ----")
    for t in technology_trends(store, top=10):
        print(f"  {t['technology']}: {t['project_count']} 个项目")
    print("=" * 60 + "\n")

    return store


if __name__ == "__main__":
    run()
