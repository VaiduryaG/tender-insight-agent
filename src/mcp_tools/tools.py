"""5 个标准化工具的业务实现。

工具函数为模块级函数：Agent 进程内（build_registry）与 MCP Server
（server.py）共用同一实现，MCP 仅是薄包装。

数据源（V1.0 实现与 README 的差异说明）：
- 结构化筛选/统计: 阶段1 parquet（原设计 SQLite，实现直接读 parquet 更简单，
  字段齐全且量小；V2.0 数据量上来后再切 SQLite，工具签名不变）
- 图谱查询: 阶段2 GraphStore（data/graph/bidding_graph.json）
- 语义搜索: 阶段3 ChromaDB（data/vector/chroma）

所有资源带模块级缓存，进程内只初始化一次。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from loguru import logger

from src.knowledge_graph.queries import (
    company_profile as _q_company_profile,
)
from src.knowledge_graph.queries import (
    find_competitors,
    group_aggregation,
    group_profile,
    industry_top_companies,
    region_overview,
    technology_trends,
)
from src.knowledge_graph.schema import NodeType
from src.knowledge_graph.store import NetworkXStore
from src.mcp_tools.registry import ToolRegistry

STAGE1_DATA = "data/processed/bidding_stage1.parquet"
GRAPH_PATH = "data/graph/bidding_graph.json"
VECTOR_DB = "data/vector/chroma"

_cache: dict = {}


def _load_df() -> pd.DataFrame:
    if "df" not in _cache:
        _cache["df"] = pd.read_parquet(STAGE1_DATA)
    return _cache["df"]


def _load_graph() -> NetworkXStore:
    if "graph" not in _cache:
        store = NetworkXStore()
        store.load(GRAPH_PATH)
        _cache["graph"] = store
    return _cache["graph"]


def _load_index():
    if "index" not in _cache:
        from src.vector_search.embedder import HashingEmbedder, ZhipuEmbedder
        from src.vector_search.search import ProjectVectorIndex

        try:
            embedder = ZhipuEmbedder()
            embedder.embed(["连通性测试"])
        except Exception as e:
            logger.warning("智谱Embedding不可用，降级离线实现: {}", str(e)[:60])
            embedder = HashingEmbedder()
        _cache["index"] = ProjectVectorIndex(embedder, VECTOR_DB)
    return _cache["index"]


# ---------------------------------------------------------------------------
# 工具 1: search_projects —— 精确条件筛选
# ---------------------------------------------------------------------------


def search_projects(
    industry: str | None = None,
    province: str | None = None,
    keyword: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """按行业/省份/关键词/金额区间筛选中标项目。

    关键词匹配（IT-025）：LLM 常把口语短语整串传来（如"学校器材"），
    纯子串匹配必然 0 结果。三级降级策略：
    1. 分词（jieba）后 AND 匹配（所有词都出现）
    2. AND 无结果 → OR 匹配（任一词命中）
    3. 仍无结果 → 自动回退 semantic_search 语义检索（不要求字面命中），
       返回 fallback_to_semantic 提示
    """
    df = _load_df()
    match_mode = ""
    terms: list[str] = []
    if industry:
        df = df[df["industry"] == industry]
    if province:
        df = df[df["province"] == province]
    if keyword:
        import jieba

        terms = [t for t in jieba.cut(keyword) if len(t) >= 2][:8]  # 跳过单字噪声
        if terms:
            and_mask = df["title"].map(lambda s: all(t in s for t in terms))
            if and_mask.any():
                df = df[and_mask]
                match_mode = "AND"
            else:
                or_mask = df["title"].map(lambda s: any(t in s for t in terms))
                if or_mask.any():
                    df = df[or_mask]
                    match_mode = "OR"
    if min_amount is not None:
        df = df[df["amount_normalized"] >= float(min_amount)]
    if max_amount is not None:
        df = df[df["amount_normalized"] <= float(max_amount)]
    df = df.sort_values("amount_normalized", ascending=False).head(int(top_k))
    result: dict[str, Any] = {
        "total_matched": len(df),
        "projects": [
            {
                "title": str(r["title"]),
                "company": str(r["canonical_name"]),
                "group": str(r["group_name"]),
                "amount": float(r["amount_normalized"]),
                "date": str(r["announcement_date_normalized"]),
                "industry": str(r["industry"]),
                "region": f"{r['province']}{r['city']}",
            }
            for _, r in df.iterrows()
        ],
    }
    if match_mode and keyword:
        result["keyword_match_mode"] = f"{match_mode}（已拆词：{' / '.join(terms)}）"

    # IT-025 三级降级第 3 级：精确检索仍 0 结果 → 自动回退语义检索。
    # 模型（尤其 glm-4-flash）拿到空列表往往直接放弃回答，工具层兜底
    # 保证"用户随口一问"也能拿到最接近的数据
    if not result["projects"] and keyword:
        try:
            sem = semantic_search(query=keyword, top_k=int(top_k))
            if sem["results"]:
                result["total_matched"] = len(sem["results"])
                result["projects"] = sem["results"]
                result["keyword_match_mode"] = "semantic_fallback（精确检索无结果，已回退语义检索，按相似度排序）"
                result["note"] = "以下为语义最接近的项目，标题可能不含字面关键词，请向用户说明"
        except Exception:
            pass  # 语义索引不可用时保持空结果原样返回

    return result


# ---------------------------------------------------------------------------
# 工具 2: company_profile —— 公司/集团画像
# ---------------------------------------------------------------------------


def company_profile_lookup(company: str) -> dict[str, Any]:
    """公司画像；集团名/简称提问时自动聚合成员公司。"""
    store = _load_graph()
    name = company.strip()
    profile = _q_company_profile(store, name)
    if profile.get("exists"):
        return profile
    # 未精确命中：解析集团简称（"中国联通"→"中国联合网络通信"）
    df = _load_df()
    group = _resolve_group(name, df)
    if group:
        return group_profile(store, group)
    # 再试公司名模糊匹配
    hit = df[df["company_name"].str.contains(name, na=False)]
    if not hit.empty:
        return _q_company_profile(store, hit.iloc[0]["canonical_name"])
    return {
        "query": name,
        "exists": False,
        "hint": "未找到该公司，可尝试集团名或查看 group_aggregation 的集团列表",
    }


# ---------------------------------------------------------------------------
# 工具 3: graph_query —— 图谱关系查询
# ---------------------------------------------------------------------------


def graph_query(
    query_type: str,
    company: str | None = None,
    industry: str | None = None,
    top: int = 10,
) -> dict[str, Any]:
    """竞对/行业排行/技术热度/地区分布/集团对比 的统一入口。"""
    store = _load_graph()
    top = int(top or 10)
    if query_type == "competitors":
        if not company:
            return {"error": "competitors 需要提供 company 参数"}
        # 精确名未命中时回退集团维度（简称"中国联通"→"中国联合网络通信"）
        name = company.strip()
        if store.get_node(NodeType.COMPANY, name) is None:
            group = _resolve_group(name, _load_df())
            if group:
                gp = group_profile(store, group)
                if gp.get("exists"):
                    return {"company": name, "group": gp["group"],
                            "competitors": gp.get("competitors", []),
                            "note": "输入为集团简称，已聚合成员分公司竞对"}
        return {"competitors": find_competitors(store, name)[:top]}
    if query_type == "industry_top":
        if not industry:
            return {"error": "industry_top 需要提供 industry 参数"}
        return {"ranking": industry_top_companies(store, industry, top)}
    if query_type == "tech_trends":
        return {"trends": technology_trends(store, top)}
    if query_type == "region_overview":
        return {"regions": region_overview(store)[:top]}
    if query_type == "group_aggregation":
        return {"groups": group_aggregation(store)[:top]}
    return {"error": f"未知 query_type: {query_type}"}


# ---------------------------------------------------------------------------
# 工具 4: semantic_search —— 语义搜索
# ---------------------------------------------------------------------------


def semantic_search(query: str, top_k: int = 5, industry: str | None = None) -> dict[str, Any]:
    """按语义模糊搜索相似项目（不要求标题含关键词）。"""
    hits = _load_index().search(query, top_k=int(top_k), industry=industry)
    return {
        "results": [
            {
                "title": h.get("title", ""),
                "similarity": h.get("similarity"),
                "company": h.get("company", ""),
                "amount": h.get("amount", 0.0),
                "industry": h.get("industry", ""),
            }
            for h in hits
        ]
    }


# ---------------------------------------------------------------------------
# 工具 5: industry_stats —— 统计汇总
# ---------------------------------------------------------------------------


def industry_stats(group_by: str, top_k: int = 10) -> dict[str, Any]:
    """按行业/省份/集团维度分组统计项目数量与中标总金额。"""
    df = _load_df()
    if group_by not in ("industry", "province", "group_name"):
        return {"error": f"未知分组维度: {group_by}"}
    stats = (
        df.groupby(group_by)
        .agg(project_count=("title", "count"), total_amount=("amount_normalized", "sum"))
        .sort_values("total_amount", ascending=False)
        .head(int(top_k))
    )
    return {
        "dimension": group_by,
        "stats": [
            {
                "name": str(name),
                "project_count": int(row["project_count"]),
                "total_amount": float(row["total_amount"]),
            }
            for name, row in stats.iterrows()
        ],
    }


# ---------------------------------------------------------------------------
# 注册表（Agent 进程内调用用；MCP Server 用 server.py 薄包装）
# ---------------------------------------------------------------------------

TOOL_DEFS = {
    "search_projects": {
        "description": "按行业/省份/关键词/金额区间筛选中标项目，返回项目列表",
        "parameters": {
            "type": "object",
            "properties": {
                "industry": {"type": "string", "description": "行业，如 智慧教育/智能交通"},
                "province": {"type": "string", "description": "省份，如 山东"},
                "keyword": {"type": "string", "description": "项目标题关键词"},
                "min_amount": {"type": "number", "description": "最低中标金额（元）"},
                "max_amount": {"type": "number", "description": "最高中标金额（元）"},
                "top_k": {"type": "integer", "description": "返回条数上限，默认10"},
            },
        },
    },
    "company_profile": {
        "description": "查询公司/集团画像：所属集团、中标统计、行业分布、竞对名单、近期项目。输入集团名（如「中国电信」）时自动聚合集团下所有分公司",
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "公司名或集团名（分公司全称会自动对齐）"},
            },
            "required": ["company"],
        },
    },
    "graph_query": {
        "description": "知识图谱关系查询。query_type: competitors=公司竞对 / industry_top=行业金额排行 / tech_trends=技术需求热度 / region_overview=地区分布 / group_aggregation=集团对比",
        "parameters": {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["competitors", "industry_top", "tech_trends", "region_overview", "group_aggregation"],
                },
                "company": {"type": "string", "description": "query_type=competitors 时的公司名"},
                "industry": {"type": "string", "description": "query_type=industry_top 时的行业名"},
                "top": {"type": "integer", "description": "返回条数上限，默认10"},
            },
            "required": ["query_type"],
        },
    },
    "semantic_search": {
        "description": "按语义模糊搜索相似项目（不要求标题含关键词），支持行业过滤",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "自然语言描述，如 校园信息化建设"},
                "top_k": {"type": "integer", "description": "返回条数，默认5"},
                "industry": {"type": "string", "description": "可选，限定行业"},
            },
            "required": ["query"],
        },
    },
    "industry_stats": {
        "description": "按维度分组统计项目数量与中标总金额。group_by: industry=行业 / province=省份 / group_name=集团",
        "parameters": {
            "type": "object",
            "properties": {
                "group_by": {"type": "string", "enum": ["industry", "province", "group_name"]},
                "top_k": {"type": "integer", "description": "返回条数上限，默认10"},
            },
            "required": ["group_by"],
        },
    },
}

_TOOL_FUNCS = {
    "search_projects": search_projects,
    "company_profile": company_profile_lookup,
    "graph_query": graph_query,
    "semantic_search": semantic_search,
    "industry_stats": industry_stats,
}


def _resolve_group(name: str, df: pd.DataFrame) -> str | None:
    """解析用户输入的公司/集团简称 → 数据集中的集团名。

    两级匹配：
    1. 子串包含（"中国电信" ⊂ "中国电信"）
    2. 字符集包含且唯一命中（简称"中国联通" ⊆ 集团"中国联合网络通信"的字符集，
       联通二字在集团全称中不相邻，子串匹配会漏）
    """
    name = name.strip()
    groups = list(df["group_name"].unique())
    hits = [g for g in groups if name in g]
    if len(hits) == 1:
        return hits[0]
    if not hits and len(name) >= 3:
        chars = set(name)
        hits = [g for g in groups if chars <= set(g)]
        if len(hits) == 1:
            return hits[0]
    return None


def build_registry() -> ToolRegistry:
    """从 TOOL_DEFS 构建进程内工具注册表（定义与实现集中在上方）。"""
    reg = ToolRegistry()
    for name, spec in TOOL_DEFS.items():
        reg.register(name=name, description=spec["description"], parameters=spec["parameters"])(_TOOL_FUNCS[name])
    return reg
