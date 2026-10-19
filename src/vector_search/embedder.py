"""文本向量化：抽象基类 + 两个实现。

- ZhipuEmbedder:  智谱AI Embedding API（V1.0 默认，无需下载本地模型）
- HashingEmbedder: 离线兜底实现（特征哈希），无网络/无 API Key 时使用，
                   保证管线永远可跑、测试不依赖网络
"""

from __future__ import annotations

import hashlib
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
from dotenv import dotenv_values
from loguru import logger

ENV_PATH = Path(".env")


class BaseEmbedder(ABC):
    """向量化接口：批量文本 -> 定长浮点向量。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    @abstractmethod
    def name(self) -> str: ...


class ZhipuEmbedder(BaseEmbedder):
    """智谱AI Embedding（默认 embedding-3，2048 维，支持中文）。

    批量请求自动分批（API 单批上限 64 条），指数退避重试。
    """

    BATCH_SIZE = 64
    MAX_RETRIES = 3

    def __init__(self, model: str = "embedding-3", api_key: str | None = None):
        self.model = model
        self._key = api_key or self._load_key()
        self._client = None

    @staticmethod
    def _load_key() -> str | None:
        key = os.getenv("ZHIPU_API_KEY") or (
            dotenv_values(ENV_PATH).get("ZHIPU_API_KEY") if ENV_PATH.exists() else None
        )
        return (key or "").strip() or None

    @property
    def name(self) -> str:
        return f"zhipu:{self.model}"

    def _get_client(self):
        if self._client is None:
            if not self._key:
                raise RuntimeError("ZHIPU_API_KEY 未配置（.env 或环境变量）")
            from zhipuai import ZhipuAI  # 延迟导入

            self._client = ZhipuAI(api_key=self._key)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[start : start + self.BATCH_SIZE]
            vectors.extend(self._embed_batch_with_retry(batch))
        return vectors

    def _embed_batch_with_retry(self, batch: list[str]) -> list[list[float]]:
        last_err: Exception | None = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                resp = self._get_client().embeddings.create(
                    model=self.model, input=batch
                )
                ordered = sorted(resp.data, key=lambda d: d.index)
                return [d.embedding for d in ordered]
            except Exception as e:  # 网络抖动/限流：指数退避后重试
                last_err = e
                wait = 2**attempt
                logger.warning("Embedding 批次失败（第{}次），{}s 后重试: {}", attempt, wait, e)
                import time

                time.sleep(wait)
        raise RuntimeError(f"Embedding 请求连续 {self.MAX_RETRIES} 次失败") from last_err


class HashingEmbedder(BaseEmbedder):
    """离线兜底：字符 n-gram 特征哈希 + L2 归一化。

    语义质量弱于真实 Embedding，但完全离线、确定性，用于测试与降级运行。
    """

    def __init__(self, dim: int = 512, ngram: int = 2):
        self.dim = dim
        self.ngram = ngram

    @property
    def name(self) -> str:
        return f"hashing-{self.ngram}gram-{self.dim}d"

    def _one(self, text: str) -> list[float]:
        vec = np.zeros(self.dim, dtype=np.float32)
        clean = re.sub(r"\s+", "", str(text))
        grams = {clean[i : i + self.ngram] for i in range(max(len(clean) - self.ngram + 1, 1))}
        grams.add(clean[:1])  # 短文本至少保留首字符特征
        for g in grams:
            h = int(hashlib.md5(g.encode()).hexdigest(), 16)
            idx, sign = h % self.dim, 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign
        norm = float(np.linalg.norm(vec))
        return (vec / norm if norm > 0 else vec).tolist()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（供离线验证用）。"""
    va, vb = np.asarray(a), np.asarray(b)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(np.dot(va, vb) / denom) if denom else 0.0
