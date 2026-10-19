"""阶段2知识图谱测试。

运行: ./run.sh -m pytest tests/test_stage2.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from src.knowledge_graph.builder import build_graph
from src.knowledge_graph.queries import (
    company_profile,
    find_competitors,
    graph_overview,
    group_aggregation,
    industry_top_companies,
    technology_trends,
)
from src.knowledge_graph.schema import NodeType, RelationType, project_key
from src.knowledge_graph.store import GraphStore, NetworkXStore


# ---------------------------------------------------------------------------
# 测试夹具：两行迷你数据（两家公司同行业竞标，一家独行业）
# ---------------------------------------------------------------------------


def _mini_df() -> pd.DataFrame:
    """A/B 同在"智慧教育"行业竞争，C 独占"智慧医疗"。"""
    rows = [
        {
            "title": "项目一",
            "canonical_name": "甲公司有限公司",
            "group_name": "甲公司",
            "amount_normalized": 100.0,
            "announcement_date_normalized": "2026-09-01",
            "project_id": "P-001",
            "industry": "智慧教育",
            "province": "山东",
            "city": "济南",
            "tech_tags_list": ["AI", "物联网"],
            "title_normalized": "项目一",
            "summary_normalized": "",
        },
        {
            "title": "项目二",
            "canonical_name": "乙公司股份有限公司",
            "group_name": "乙公司",
            "amount_normalized": 200.0,
            "announcement_date_normalized": "2026-09-02",
            "project_id": "P-002",
            "industry": "智慧教育",
            "province": "山东",
            "city": "青岛",
            "tech_tags_list": ["AI"],
            "title_normalized": "项目二",
            "summary_normalized": "",
        },
        {
            "title": "项目三",
            "canonical_name": "丙公司有限责任公司",
            "group_name": "丙公司",
            "amount_normalized": 300.0,
            "announcement_date_normalized": "2026-09-03",
            "project_id": "",
            "industry": "智慧医疗",
            "province": "江苏",
            "city": "南京",
            "tech_tags_list": [],
            "title_normalized": "项目三",
            "summary_normalized": "",
        },
    ]
    return pd.DataFrame(rows)


@pytest.fixture()
def store() -> GraphStore:
    s = NetworkXStore()
    return build_graph(_mini_df(), s)


# ---------------------------------------------------------------------------
# store 基础行为
# ---------------------------------------------------------------------------


def test_node_crud_and_merge():
    s = NetworkXStore()
    s.add_node(NodeType.COMPANY, "甲", {"group": "甲集团"})
    s.add_node(NodeType.COMPANY, "甲", {"province": "山东"})  # 重复添加合并属性
    node = s.get_node(NodeType.COMPANY, "甲")
    assert node == {"group": "甲集团", "province": "山东"}
    assert s.get_node(NodeType.COMPANY, "不存在") is None


def test_add_edge_requires_nodes():
    s = NetworkXStore()
    s.add_node(NodeType.COMPANY, "甲")
    with pytest.raises(KeyError):
        s.add_edge(
            (NodeType.COMPANY, "甲"), (NodeType.PROJECT, "P"), RelationType.WON, {}
        )


def test_same_rel_type_edge_merges():
    s = NetworkXStore()
    s.add_node(NodeType.COMPANY, "甲")
    s.add_node(NodeType.PROJECT, "P")
    s.add_edge((NodeType.COMPANY, "甲"), (NodeType.PROJECT, "P"), RelationType.WON, {"amount": 1})
    s.add_edge((NodeType.COMPANY, "甲"), (NodeType.PROJECT, "P"), RelationType.WON, {"date": "d"})
    edges = s.edges(RelationType.WON)
    assert len(edges) == 1 and edges[0][2] == {"amount": 1, "date": "d"}


# ---------------------------------------------------------------------------
# 图谱构建与查询
# ---------------------------------------------------------------------------


def test_build_stats(store):
    overview = graph_overview(store)
    assert overview["nodes"] == {
        "Company": 3, "Industry": 2, "Project": 3, "Region": 3, "Technology": 2,
    }
    # WON/BELONGS_TO/LOCATED_IN/REQUIRES 各按行数；COMPETES_WITH 双向边
    assert overview["edges"]["WON"] == 3
    assert overview["edges"]["BELONGS_TO"] == 3
    assert overview["edges"]["REQUIRES"] == 3  # AI*2 + 物联网*1
    assert overview["edges"]["COMPETES_WITH"] == 2  # 甲<->乙 双向


def test_project_key_idempotent():
    assert project_key("P-1", "标题") == project_key("P-1", "标题")
    # 无编号时按标题哈希兜底，不同标题不同键
    assert project_key("", "标题A") != project_key("", "标题B")


def test_company_profile(store):
    profile = company_profile(store, "甲公司有限公司")
    assert profile["exists"]
    assert profile["group"] == "甲公司"
    assert profile["project_count"] == 1
    assert profile["total_amount"] == 100.0
    assert profile["industry_distribution"] == {"智慧教育": 1}
    assert profile["competitors"][0]["company"] == "乙公司股份有限公司"


def test_find_competitors_only_same_industry(store):
    assert set(c["company"] for c in find_competitors(store, "甲公司有限公司")) == {
        "乙公司股份有限公司"
    }
    # 丙公司独占行业，无竞对
    assert find_competitors(store, "丙公司有限责任公司") == []


def test_industry_top_companies(store):
    top = industry_top_companies(store, "智慧教育")
    assert [t["company"] for t in top] == ["乙公司股份有限公司", "甲公司有限公司"]  # 按金额降序
    assert top[0]["total_amount"] == 200.0


def test_technology_trends(store):
    trends = {t["technology"]: t["project_count"] for t in technology_trends(store)}
    assert trends == {"AI": 2, "物联网": 1}


def test_group_aggregation(store):
    groups = {g["group"]: g for g in group_aggregation(store)}
    assert groups["甲公司"]["total_amount"] == 100.0
    assert groups["乙公司"]["companies"] == ["乙公司股份有限公司"]


# ---------------------------------------------------------------------------
# 持久化与接口可替换性
# ---------------------------------------------------------------------------


def test_save_load_roundtrip(tmp_path):
    original = NetworkXStore()
    build_graph(_mini_df(), original)
    path = tmp_path / "graph.json"
    original.save(path)

    loaded = NetworkXStore()
    loaded.load(path)
    assert graph_overview(loaded) == graph_overview(original)
    assert company_profile(loaded, "甲公司有限公司") == company_profile(
        original, "甲公司有限公司"
    )


def test_store_interface_contract():
    """NetworkXStore 必须实现 GraphStore 全部抽象方法（接口可替换性保证）。"""
    abstract = set(GraphStore.__abstractmethods__)
    assert abstract.issubset(set(dir(NetworkXStore)))


def test_subgraph_structure(store):
    # depth=1：公司 -> 它中标的项目（及反向竞对公司）
    ego1 = store.subgraph(NodeType.COMPANY, "甲公司有限公司", depth=1)
    node_types1 = {n["type"] for n in ego1["nodes"]}
    assert {"Company", "Project"} <= node_types1
    assert "WON" in {e["type"] for e in ego1["edges"]}
    # depth=2：项目 -> 行业/技术/地区
    ego2 = store.subgraph(NodeType.COMPANY, "甲公司有限公司", depth=2)
    node_types2 = {n["type"] for n in ego2["nodes"]}
    assert {"Company", "Project", "Technology", "Industry", "Region"} <= node_types2
