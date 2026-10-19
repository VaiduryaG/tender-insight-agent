"""常用图查询封装：全部基于 GraphStore 接口，不感知底层实现。

这些函数即 MCP 工具层 graph_query / company_profile 的数据来源。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.knowledge_graph.schema import NodeType, RelationType
from src.knowledge_graph.store import GraphStore


def company_projects(store: GraphStore, company: str) -> list[dict[str, Any]]:
    """公司中标历史：按日期倒序。"""
    projects = []
    for pkey, ntype, props in store.neighbors(NodeType.COMPANY, company, RelationType.WON):
        if ntype is not NodeType.PROJECT:
            continue
        detail = store.get_node(NodeType.PROJECT, pkey) or {}
        projects.append(
            {
                "project_key": pkey,
                "title": detail.get("title", pkey),
                "amount": props.get("amount") or detail.get("amount"),
                "date": props.get("date") or detail.get("date"),
                "industry": detail.get("industry"),
                "summary": detail.get("summary", ""),
            }
        )
    return sorted(projects, key=lambda p: p.get("date") or "", reverse=True)


def company_profile(store: GraphStore, company: str) -> dict[str, Any]:
    """公司画像：中标统计 + 行业分布 + 竞对名单。"""
    node = store.get_node(NodeType.COMPANY, company)
    if node is None:
        return {"company": company, "exists": False}

    projects = company_projects(store, company)
    amounts = [p["amount"] for p in projects if p.get("amount")]
    industry_count: dict[str, int] = defaultdict(int)
    for p in projects:
        pkey = p["project_key"]
        for bkey, ntype, _ in store.neighbors(NodeType.PROJECT, pkey, RelationType.BELONGS_TO):
            if ntype is NodeType.INDUSTRY:
                industry_count[bkey] += 1

    competitors = [
        {
            "company": ckey,
            "group": (store.get_node(NodeType.COMPANY, ckey) or {}).get("group", ""),
            "shared_industries": props.get("shared_industries", []),
        }
        for ckey, _, props in store.neighbors(NodeType.COMPANY, company, RelationType.COMPETES_WITH)
    ]

    return {
        "company": company,
        "exists": True,
        "group": node.get("group", company),
        "project_count": len(projects),
        "total_amount": sum(amounts) if amounts else 0.0,
        "industry_distribution": dict(sorted(industry_count.items(), key=lambda x: -x[1])),
        "competitors": competitors,
        "recent_projects": projects[:5],
    }


def group_profile(store: GraphStore, group: str) -> dict[str, Any]:
    """集团画像：聚合集团下所有成员公司的中标情况。

    用于用户以集团名（如"中国电信"）提问的场景——分公司各自是独立法人
    实体，但业务上应聚合到集团维度回答。
    """
    members = [
        key
        for key, props in store.nodes(NodeType.COMPANY)
        if (props.get("group") or key) == group
    ]
    if not members:
        return {"group": group, "exists": False}

    profiles = [company_profile(store, m) for m in members]
    industry_count: dict[str, int] = defaultdict(int)
    competitor_industries: dict[str, set[str]] = defaultdict(set)
    all_projects: list[dict[str, Any]] = []
    for m, p in zip(members, profiles):
        for ind, n in p.get("industry_distribution", {}).items():
            industry_count[ind] += n
        for c in p.get("competitors", []):
            # 保留共同竞争领域（Company 节点聚合时会丢失 shared_industries 信息）
            for ind in c.get("shared_industries", []):
                competitor_industries[c["company"]].add(ind)
            if not c.get("shared_industries"):
                competitor_industries[c["company"]].add(c["group"] or c["company"])
        all_projects.extend(p.get("recent_projects", []))

    amounts = [proj["amount"] for proj in all_projects if proj.get("amount")]
    all_projects.sort(key=lambda proj: proj.get("date") or "", reverse=True)

    competitors = [
        {"company": comp, "shared_industries": sorted(inds)}
        for comp, inds in sorted(competitor_industries.items())
    ]

    return {
        "group": group,
        "exists": True,
        "member_companies": members,
        "project_count": sum(p.get("project_count", 0) for p in profiles),
        "total_amount": sum(p.get("total_amount", 0.0) for p in profiles),
        "avg_amount": (sum(amounts) / len(amounts)) if amounts else 0.0,
        "industry_distribution": dict(sorted(industry_count.items(), key=lambda x: -x[1])),
        "competitors": competitors,
        "recent_projects": all_projects[:5],
    }


def find_competitors(store: GraphStore, company: str) -> list[dict[str, Any]]:
    """竞对名单：按共同竞争行业数排序。"""
    result = [
        {
            "company": ckey,
            "group": (store.get_node(NodeType.COMPANY, ckey) or {}).get("group", ""),
            "shared_industries": props.get("shared_industries", []),
        }
        for ckey, _, props in store.neighbors(
            NodeType.COMPANY, company, RelationType.COMPETES_WITH, "out"
        )
    ]
    return sorted(result, key=lambda c: -len(c["shared_industries"]))


def industry_top_companies(
    store: GraphStore, industry: str, top: int = 10
) -> list[dict[str, Any]]:
    """行业内公司中标金额排行。"""
    amounts: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for proj, ind, _ in store.edges(RelationType.BELONGS_TO):
        if ind != industry:
            continue
        for comp, _, won_props in store.neighbors(NodeType.PROJECT, proj, RelationType.WON, "in"):
            amounts[comp] += won_props.get("amount") or 0.0
            counts[comp] += 1
    ranked = sorted(amounts.items(), key=lambda x: -x[1])[:top]
    return [
        {
            "company": comp,
            "group": (store.get_node(NodeType.COMPANY, comp) or {}).get("group", ""),
            "total_amount": amount,
            "project_count": counts[comp],
        }
        for comp, amount in ranked
    ]


def technology_trends(store: GraphStore, top: int = 15) -> list[dict[str, Any]]:
    """技术需求热度：按 REQUIRES 边数量统计。"""
    counts: dict[str, int] = defaultdict(int)
    for _, tech, _ in store.edges(RelationType.REQUIRES):
        counts[tech] += 1
    return [
        {"technology": tech, "project_count": n}
        for tech, n in sorted(counts.items(), key=lambda x: -x[1])[:top]
    ]


def region_overview(store: GraphStore) -> list[dict[str, Any]]:
    """地区概览：各省项目数与中标总金额。"""
    provinces: dict[str, dict[str, float]] = defaultdict(lambda: {"projects": 0, "amount": 0.0})
    for proj, region, _ in store.edges(RelationType.LOCATED_IN):
        node = store.get_node(NodeType.REGION, region) or {}
        prov = node.get("province") or region
        provinces[prov]["projects"] += 1
        for comp, _, won in store.neighbors(NodeType.PROJECT, proj, RelationType.WON, "in"):
            provinces[prov]["amount"] += won.get("amount") or 0.0
    return [
        {"province": prov, "project_count": int(v["projects"]), "total_amount": v["amount"]}
        for prov, v in sorted(provinces.items(), key=lambda x: -x[1]["amount"])
    ]


def group_aggregation(store: GraphStore) -> list[dict[str, Any]]:
    """集团维度聚合：集团 -> 中标总额/项目数/成员公司。"""
    groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total_amount": 0.0, "project_count": 0, "companies": set()}
    )
    for comp, _, won in store.edges(RelationType.WON):
        node = store.get_node(NodeType.COMPANY, comp) or {}
        g = node.get("group") or comp
        groups[g]["total_amount"] += won.get("amount") or 0.0
        groups[g]["project_count"] += 1
        groups[g]["companies"].add(comp)
    return [
        {
            "group": g,
            "total_amount": v["total_amount"],
            "project_count": v["project_count"],
            "companies": sorted(v["companies"]),
        }
        for g, v in sorted(groups.items(), key=lambda x: -x[1]["total_amount"])
    ]


def graph_overview(store: GraphStore) -> dict[str, Any]:
    """图规模总览（Web 数据概览页与 Agent 判断数据规模用）。"""
    return store.stats()
