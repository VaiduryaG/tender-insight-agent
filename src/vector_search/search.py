"""项目向量索引：ChromaDB 持久化存储 + 语义搜索。

对每个项目拼接「标题 + 行业 + 技术 + 摘要」作为被检索文本，元数据冗余
常用过滤字段（行业/省份/金额/日期），支持混合检索（语义 + 元数据过滤）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb
import pandas as pd
from loguru import logger

from src.vector_search.embedder import BaseEmbedder

DEFAULT_DB_PATH = Path("data/vector/chroma")


def project_text(row: pd.Series) -> str:
    """构造项目被检索文本：标题为主，行业/技术/摘要为辅。"""
    tags = row.get("tech_tags_list")
    tags = list(tags) if tags is not None and not (isinstance(tags, float) and pd.isna(tags)) else []
    parts = [
        str(row.get("title_normalized") or row.get("title", "")),
        str(row.get("industry", "")),
        " ".join(str(t) for t in tags),
        str(row.get("summary_normalized") or row.get("summary", "")),
    ]
    return "；".join(p for p in parts if p and p.lower() != "nan")


class ProjectVectorIndex:
    """ChromaDB 项目索引。同一持久化目录重复 build 是幂等的（按 id upsert）。"""

    def __init__(self, embedder: BaseEmbedder, db_path: str | Path = DEFAULT_DB_PATH):
        self.embedder = embedder
        self.db_path = Path(db_path)
        self.client = chromadb.PersistentClient(path=str(self.db_path))
        self.collection = self.client.get_or_create_collection(
            name="projects",
            metadata={"hnsw:space": "cosine", "embedder": embedder.name},
        )

    # ---- 索引构建 ----------------------------------------------------------

    def build(self, df: pd.DataFrame) -> int:
        """全量构建/更新索引（按 project key upsert），返回索引条数。"""
        ids, docs, metas = [], [], []
        for idx, row in df.iterrows():
            pid = self._row_id(row, idx)
            ids.append(pid)
            docs.append(project_text(row))
            metas.append(self._row_meta(row))

        if ids:
            # 分批 upsert，避免大 DataFrame 一次性传入
            step = 500
            for start in range(0, len(ids), step):
                sl = slice(start, start + step)
                self.collection.upsert(
                    ids=ids[sl], documents=docs[sl], metadatas=metas[sl],
                    embeddings=self.embedder.embed(docs[sl]),
                )
        logger.info("向量索引构建完成: {} 条 (embedder={})", self.collection.count(), self.embedder.name)
        return self.collection.count()

    @staticmethod
    def _row_id(row: pd.Series, idx: int) -> str:
        """项目 id：优先采购编号；缺失时与图谱 project_key 一致，用标题哈希兜底。"""
        pid = str(row.get("project_id", "") or "").strip()
        if pid:
            return pid
        import hashlib

        title = str(row.get("title_normalized") or row.get("title", "")) or str(idx)
        return "hash-" + hashlib.md5(title.strip().encode()).hexdigest()[:12]

    @staticmethod
    def _row_meta(row: pd.Series) -> dict[str, Any]:
        amount = row.get("amount_normalized")
        return {
            "title": str(row.get("title_normalized") or row.get("title", "")),
            "company": str(row.get("canonical_name", "")),
            "group": str(row.get("group_name", "")),
            "industry": str(row.get("industry", "")),
            "province": str(row.get("province", "")),
            "city": str(row.get("city", "")),
            "amount": float(amount) if pd.notna(amount) else 0.0,
            "date": str(row.get("announcement_date_normalized") or ""),
        }

    # ---- 检索 --------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 5,
        industry: str | None = None,
        province: str | None = None,
        min_amount: float | None = None,
    ) -> list[dict[str, Any]]:
        """语义搜索。可选元数据过滤（行业/省份/最低金额）。

        注意：查询向量必须由建索引用的同一 embedder 生成后以
        query_embeddings 传入——若传 query_texts，ChromaDB 会退回内置
        ONNX 模型（384维），与智谱 2048 维向量不匹配。
        """
        n = self.collection.count()
        if n == 0:
            return []

        query_embedding = self.embedder.embed([query])[0]
        where = self._build_where(industry, province, min_amount)
        res = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, n),
            where=where,
        )
        hits = []
        for i in range(len(res["ids"][0])):
            hits.append(
                {
                    "id": res["ids"][0][i],
                    "text": res["documents"][0][i],
                    "distance": round(res["distances"][0][i], 4),
                    "similarity": round(1 - res["distances"][0][i], 4),
                    **(res["metadatas"][0][i] or {}),
                }
            )
        return hits

    @staticmethod
    def _build_where(
        industry: str | None, province: str | None, min_amount: float | None
    ) -> dict[str, Any] | None:
        conds: list[dict[str, Any]] = []
        if industry:
            conds.append({"industry": {"$eq": industry}})
        if province:
            conds.append({"province": {"$eq": province}})
        if min_amount is not None:
            conds.append({"amount": {"$gte": min_amount}})
        if not conds:
            return None
        return conds[0] if len(conds) == 1 else {"$and": conds}

    def count(self) -> int:
        return self.collection.count()
