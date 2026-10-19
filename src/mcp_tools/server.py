"""MCP Server：将 5 个工具通过 MCP 协议（stdio）对外暴露。

实现复用 tools.py 的业务函数——本文件只是协议适配层（薄包装）。
Agent 进程内调用走 build_registry()；外部客户端（Claude Desktop 等）
通过 stdio 与本 server 通信。

启动: ./run.sh scripts/run_mcp_server.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# mcp 2.x: FastMCP 重命名为 MCPServer
from mcp.server.mcpserver import MCPServer

from src.mcp_tools import tools as T

mcp = MCPServer("bidding-agent")


def _json(result) -> str:
    """统一序列化：日期/金额等类型安全转字符串。"""
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
def search_projects(
    industry: str | None = None,
    province: str | None = None,
    keyword: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    top_k: int = 10,
) -> str:
    """按行业/省份/关键词/金额区间筛选中标项目，返回项目列表。"""
    return _json(
        T.search_projects(industry=industry, province=province, keyword=keyword,
                          min_amount=min_amount, max_amount=max_amount, top_k=top_k)
    )


@mcp.tool()
def company_profile(company: str) -> str:
    """查询公司/集团画像：所属集团、中标统计、行业分布、竞对名单、近期项目。输入集团名（如「中国电信」）时自动聚合集团下所有分公司。"""
    return _json(T.company_profile_lookup(company))


@mcp.tool()
def graph_query(
    query_type: str,
    company: str | None = None,
    industry: str | None = None,
    top: int = 10,
) -> str:
    """知识图谱关系查询。query_type: competitors=公司竞对 / industry_top=行业金额排行 / tech_trends=技术需求热度 / region_overview=地区分布 / group_aggregation=集团对比。"""
    return _json(T.graph_query(query_type=query_type, company=company, industry=industry, top=top))


@mcp.tool()
def semantic_search(query: str, top_k: int = 5, industry: str | None = None) -> str:
    """按语义模糊搜索相似项目（不要求标题含关键词），支持行业过滤。"""
    return _json(T.semantic_search(query=query, top_k=top_k, industry=industry))


@mcp.tool()
def industry_stats(group_by: str, top_k: int = 10) -> str:
    """按维度分组统计项目数量与中标总金额。group_by: industry=行业 / province=省份 / group_name=集团。"""
    return _json(T.industry_stats(group_by=group_by, top_k=top_k))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
