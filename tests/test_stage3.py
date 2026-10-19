"""阶段3向量检索测试（离线，不依赖网络与API）。

运行: ./run.sh -m pytest tests/test_stage3.py -v
真实智谱 Embedding 的连通性由 scripts/build_vector_index.py 运行时验证。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from src.vector_search.embedder import HashingEmbedder, cosine
from src.vector_search.search import ProjectVectorIndex, project_text


@pytest.fixture()
def df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "title": "智慧校园云平台建设项目",
                "title_normalized": "智慧校园云平台建设项目",
                "project_id": "P-EDU-1",
                "industry": "智慧教育",
                "province": "山东",
                "city": "济南",
                "amount_normalized": 1000000.0,
                "canonical_name": "甲公司有限公司",
                "group_name": "甲公司",
                "tech_tags_list": ["云服务", "大数据"],
                "summary_normalized": "高校智慧校园云平台建设",
            },
            {
                "title": "交通信号灯智能控制系统采购",
                "title_normalized": "交通信号灯智能控制系统采购",
                "project_id": "P-TRF-1",
                "industry": "智能交通",
                "province": "北京",
                "city": "北京",
                "amount_normalized": 500000.0,
                "canonical_name": "乙公司股份有限公司",
                "group_name": "乙公司",
                "tech_tags_list": ["物联网"],
                "summary_normalized": "交通信号控制",
            },
        ]
    )


@pytest.fixture()
def index(tmp_path, df) -> ProjectVectorIndex:
    idx = ProjectVectorIndex(HashingEmbedder(), db_path=tmp_path / "chroma")
    idx.build(df)
    return idx


# ---------------------------------------------------------------------------
# HashingEmbedder（离线兜底实现）
# ---------------------------------------------------------------------------


def test_hashing_embedder_deterministic_and_normalized():
    e = HashingEmbedder(dim=256)
    v1 = e.embed(["智慧城市大数据平台"])
    v2 = e.embed(["智慧城市大数据平台"])
    assert v1 == v2  # 确定性
    assert abs(sum(x * x for x in v1[0]) - 1.0) < 1e-5  # L2 归一化


def test_hashing_embedder_similar_vs_unrelated():
    e = HashingEmbedder()
    sim = cosine(e.embed(["智慧校园云平台"])[0], e.embed(["智慧校园云平台建设"])[0])
    unsim = cosine(e.embed(["智慧校园云平台"])[0], e.embed(["交通信号灯控制"])[0])
    assert sim > unsim


def test_cosine_identical_and_orthogonal():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 索引构建与检索
# ---------------------------------------------------------------------------


def test_build_count(index):
    assert index.count() == 2


def test_build_is_idempotent(index, df):
    """重复 build 按 id upsert，数量不变。"""
    index.build(df)
    index.build(df)
    assert index.count() == 2


def test_search_relevance(index):
    hits = index.search("智慧校园信息化", top_k=2)
    assert hits[0]["id"] == "P-EDU-1"  # 教育项目排在交通前面
    assert 0 <= hits[0]["similarity"] <= 1


def test_search_metadata_filter(index):
    hits = index.search("平台建设", top_k=5, industry="智能交通")
    assert [h["id"] for h in hits] == ["P-TRF-1"]
    hits = index.search("平台建设", top_k=5, province="山东", min_amount=900000)
    assert [h["id"] for h in hits] == ["P-EDU-1"]


def test_search_metadata_filter_combined(index):
    """组合过滤无匹配时返回空（金额条件高于该行业所有项目）。"""
    assert index.search("平台建设", top_k=5, industry="智慧教育", min_amount=9999999) == []


def test_search_empty_index(tmp_path):
    idx = ProjectVectorIndex(HashingEmbedder(), db_path=tmp_path / "empty")
    assert idx.search("任意查询") == []


def test_search_hit_metadata_complete(index):
    hit = index.search("智慧校园", top_k=1)[0]
    for field in ("title", "company", "group", "industry", "province", "amount", "date"):
        assert field in hit


# ---------------------------------------------------------------------------
# 文本构造
# ---------------------------------------------------------------------------


def test_project_text_includes_all_parts(df):
    text = project_text(df.iloc[0])
    assert "智慧校园云平台建设项目" in text
    assert "智慧教育" in text
    assert "云服务" in text


def test_project_text_handles_missing_fields():
    row = pd.Series({"title": "项目", "tech_tags_list": None})
    text = project_text(row)
    assert "项目" in text
    assert text.endswith("项目；") or "项目" == text.strip("；").strip()


def test_project_id_fallback_is_stable(index, df):
    """无 project_id 的行用标题哈希兜底（与图谱 project_key 一致），重复构建幂等。"""
    df_noid = df.copy()
    df_noid["project_id"] = None

    fresh = ProjectVectorIndex(HashingEmbedder(), db_path=index.db_path.parent / "noid")
    fresh.build(df_noid)
    first_ids = sorted(h["id"] for h in fresh.search("项目", top_k=5))
    assert all(i.startswith("hash-") for i in first_ids)

    fresh.build(df_noid)  # 同一数据再建 -> id 稳定，不产生重复
    assert fresh.count() == 2
