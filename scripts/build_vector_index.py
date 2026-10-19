"""阶段3总入口：读取阶段1标准化数据 -> 向量化 -> 写入 ChromaDB -> 演示语义搜索。

产物:
    data/vector/chroma/    ChromaDB 持久化目录

Embedder 选择策略: 有 ZHIPU_API_KEY 且网络可用时用智谱 Embedding（embedding-3），
否则自动降级到离线 HashingEmbedder（保证管线永远可跑）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from loguru import logger

from src.vector_search.embedder import HashingEmbedder, ZhipuEmbedder
from src.vector_search.search import DEFAULT_DB_PATH, ProjectVectorIndex

STAGE1_DATA = Path("data/processed/bidding_stage1.parquet")


def pick_embedder():
    """优先智谱 Embedding，失败自动降级离线实现。"""
    try:
        embedder = ZhipuEmbedder()
        _ = embedder.embed(["连通性测试"])
        logger.info("Embedder: {}", embedder.name)
        return embedder
    except Exception as e:
        logger.warning("智谱 Embedding 不可用（{}），降级离线 HashingEmbedder", str(e)[:80])
        return HashingEmbedder()


def run() -> ProjectVectorIndex:
    if not STAGE1_DATA.exists():
        logger.error("阶段1产物不存在，请先运行: ./run.sh scripts/run_stage1.py")
        sys.exit(1)

    df = pd.read_parquet(STAGE1_DATA)
    logger.info("阶段3开始: 读取 {} ({} 条)", STAGE1_DATA, len(df))

    index = ProjectVectorIndex(pick_embedder(), DEFAULT_DB_PATH)
    index.build(df)

    demo_queries = [
        ("智慧校园信息化建设", None),
        ("交通管理设备采购", None),
        ("医疗设备", "医疗卫生"),
    ]
    print("\n" + "=" * 60)
    for query, industry in demo_queries:
        hits = index.search(query, top_k=3, industry=industry)
        print(f"\n[搜索] {query!r}" + (f" (行业过滤: {industry})" if industry else ""))
        for h in hits:
            print(f"  {h['similarity']:.3f}  {h['title'][:36]}  [{h['company'][:20]}] {h['amount']:,.0f}元")
    print("=" * 60 + "\n")

    return index


if __name__ == "__main__":
    run()
