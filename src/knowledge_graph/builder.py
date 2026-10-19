"""图谱构建：从阶段1标准化数据构建「公司-项目-行业-技术-地区」图谱。

输入: data/processed/bidding_stage1.parquet（阶段1产物）
输出: 填充完毕的 GraphStore（含推导出的 COMPETES_WITH 竞对关系）
"""

from __future__ import annotations

import pandas as pd
from loguru import logger

from src.knowledge_graph.schema import NodeType, RelationType, project_key
from src.knowledge_graph.store import GraphStore


def build_graph(df: pd.DataFrame, store: GraphStore) -> GraphStore:
    """将标准化 DataFrame 写入 GraphStore。同一 store 重复调用是幂等的。"""
    for _, row in df.iterrows():
        _add_project_row(store, row)

    n_compete = _derive_competitors(store)
    logger.info(
        "图谱构建完成: 项目 {} 条 -> 竞对关系 {} 组",
        len(df),
        n_compete,
    )
    return store


def _add_project_row(store: GraphStore, row: pd.Series) -> None:
    """单条公告 -> 项目节点 + 各维度节点与关系。"""
    pkey = project_key(str(row.get("project_id", "")), str(row["title"]))
    amount = row.get("amount_normalized")
    date = row.get("announcement_date_normalized")
    company = row["canonical_name"]

    # 公司节点（法人实体级；group 作为属性支持集团维度聚合）
    store.add_node(
        NodeType.COMPANY,
        company,
        {
            "group": str(row.get("group_name", "") or company),
            "province": str(row.get("province", "")),
        },
    )

    # 项目节点
    store.add_node(
        NodeType.PROJECT,
        pkey,
        {
            "title": str(row.get("title_normalized") or row["title"]),
            "amount": float(amount) if pd.notna(amount) else None,
            "date": str(date) if pd.notna(date) else None,
            "summary": str(row.get("summary_normalized") or row.get("summary", "")),
            "purchaser": str(row.get("purchaser", "")),
            "project_id": str(row.get("project_id", "")),
        },
    )

    # 中标关系
    store.add_edge(
        (NodeType.COMPANY, company),
        (NodeType.PROJECT, pkey),
        RelationType.WON,
        {"amount": float(amount) if pd.notna(amount) else None, "date": str(date) if pd.notna(date) else None},
    )

    # 行业
    industry = str(row.get("industry") or "未分类")
    store.add_node(NodeType.INDUSTRY, industry)
    store.add_edge(
        (NodeType.PROJECT, pkey), (NodeType.INDUSTRY, industry), RelationType.BELONGS_TO
    )

    # 地区（省+市）
    region = f"{row.get('province', '')}{row.get('city', '')}"
    store.add_node(NodeType.REGION, region, {"province": str(row.get("province", "")), "city": str(row.get("city", ""))})
    store.add_edge((NodeType.PROJECT, pkey), (NodeType.REGION, region), RelationType.LOCATED_IN)

    # 技术（parquet 读回时 list 列为 numpy array，统一转 list）
    tags = row.get("tech_tags_list")
    tags = list(tags) if tags is not None and not (isinstance(tags, float) and pd.isna(tags)) else []
    for tech in tags:
        tech = str(tech).strip()
        if not tech:
            continue
        store.add_node(NodeType.TECHNOLOGY, tech)
        store.add_edge((NodeType.PROJECT, pkey), (NodeType.TECHNOLOGY, tech), RelationType.REQUIRES)


def _derive_competitors(store: GraphStore) -> int:
    """推导竞对关系：同一行业内出现过的中标公司两两互为竞对。

    关系属性 shared_industries 记录共同竞争的行业列表（双向边去重合计）。
    """
    # BELONGS_TO 边: project -> industry，得到项目所属行业
    industry_of: dict[str, str] = {}
    for proj, ind, _ in store.edges(RelationType.BELONGS_TO):
        industry_of[proj] = ind
    # WON 边: company -> project，得到行业内出现过的公司
    companies_of: dict[str, set[str]] = {}
    for comp, proj, _ in store.edges(RelationType.WON):
        if proj in industry_of:
            companies_of.setdefault(industry_of[proj], set()).add(comp)

    # 行业内公司两两连接
    pair_industries: dict[tuple[str, str], set[str]] = {}
    for ind, comps in companies_of.items():
        comps = sorted(comps)
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                a, b = comps[i], comps[j]
                for u, v in ((a, b), (b, a)):
                    pair_industries.setdefault((u, v), set()).add(ind)

    for (a, b), inds in pair_industries.items():
        store.add_edge(
            (NodeType.COMPANY, a),
            (NodeType.COMPANY, b),
            RelationType.COMPETES_WITH,
            {"shared_industries": sorted(inds)},
        )

    # 有向对合计：无向竞对组数 = 有向边组数 / 2
    return len(pair_industries) // 2 if pair_industries else 0
