"""阶段5 MCP Server 测试：通过 MCP 内存会话做真实的协议级调用。

运行: ./run.sh -m pytest tests/test_stage5.py -v

覆盖 README 验收标准「MCP Server 可启动并响应工具调用」：
list_tools 能列出 5 个工具、call_tool 能通过协议拿到正确结果。
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest
from mcp.shared.memory import create_client_server_memory_streams
from mcp import ClientSession

from src.knowledge_graph.builder import build_graph
from src.knowledge_graph.store import NetworkXStore
from src.mcp_tools import tools as tools_mod
from src.mcp_tools.server import mcp
from src.vector_search.embedder import HashingEmbedder
from src.vector_search.search import ProjectVectorIndex

EXPECTED_TOOLS = {
    "search_projects", "company_profile", "graph_query", "semantic_search", "industry_stats",
}


def _mini_df() -> pd.DataFrame:
    from tests.test_stage4 import _mini_df  # 复用阶段4夹具数据

    return _mini_df()


@pytest.fixture(scope="module")
def patched_cache(tmp_path_factory):
    """注入 mini 数据，隔离真实数据文件与智谱 Embedding。"""
    store = NetworkXStore()
    build_graph(_mini_df(), store)
    idx = ProjectVectorIndex(
        HashingEmbedder(), db_path=tmp_path_factory.mktemp("chroma") / "chroma"
    )
    idx.build(_mini_df())
    tools_mod._cache.clear()
    tools_mod._cache.update({"df": _mini_df(), "graph": store, "index": idx})
    yield
    tools_mod._cache.clear()


def _run(coro):
    return asyncio.run(coro)


def test_server_lists_all_tools(patched_cache):
    async def _list():
        # mcp 2.3.0: create_client_server_memory_streams 是异步上下文管理器
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with ClientSession(*client_streams) as session:
                # 启动服务器
                async with mcp._mcp_server.connect(server_streams[0], server_streams[1]):
                    await session.initialize()
                    result = await session.list_tools()
                    return {t.name for t in result.tools}

    assert EXPECTED_TOOLS <= _run(_list())


def test_call_search_projects_over_mcp(patched_cache):
    async def _call():
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with ClientSession(*client_streams) as session:
                async with mcp._mcp_server.connect(server_streams[0], server_streams[1]):
                    await session.initialize()
                    result = await session.call_tool(
                        "search_projects", {"industry": "智慧教育"}
                    )
                    return json.loads(result.content[0].text)

    r = _run(_call())
    assert r["total_matched"] == 1
    assert r["projects"][0]["company"] == "甲公司有限公司"


def test_call_company_profile_group_aggregation(patched_cache):
    async def _call():
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with ClientSession(*client_streams) as session:
                async with mcp._mcp_server.connect(server_streams[0], server_streams[1]):
                    await session.initialize()
                    result = await session.call_tool("company_profile", {"company": "甲集团"})
                    return json.loads(result.content[0].text)

    r = _run(_call())
    assert r["exists"] and r["total_amount"] == 100.0


def test_call_semantic_search_over_mcp(patched_cache):
    async def _call():
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with ClientSession(*client_streams) as session:
                async with mcp._mcp_server.connect(server_streams[0], server_streams[1]):
                    await session.initialize()
                    result = await session.call_tool("semantic_search", {"query": "校园信息化"})
                    return json.loads(result.content[0].text)

    r = _run(_call())
    assert r["results"][0]["company"] == "甲公司有限公司"


def test_call_graph_query_dispatch(patched_cache):
    async def _call():
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with ClientSession(*client_streams) as session:
                async with mcp._mcp_server.connect(server_streams[0], server_streams[1]):
                    await session.initialize()
                    result = await session.call_tool("graph_query", {"query_type": "tech_trends"})
                    return json.loads(result.content[0].text)

    r = _run(_call())
    assert {t["technology"] for t in r["trends"]} == {"AI", "物联网"}


def test_call_industry_stats_over_mcp(patched_cache):
    async def _call():
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with ClientSession(*client_streams) as session:
                async with mcp._mcp_server.connect(server_streams[0], server_streams[1]):
                    await session.initialize()
                    result = await session.call_tool("industry_stats", {"group_by": "province"})
                    return json.loads(result.content[0].text)

    r = _run(_call())
    assert {s["name"] for s in r["stats"]} == {"山东", "北京"}


def test_call_error_handled_gracefully(patched_cache):
    """协议层调用未知工具/坏参数不崩溃，返回 isError。"""
    async def _call():
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with ClientSession(*client_streams) as session:
                async with mcp._mcp_server.connect(server_streams[0], server_streams[1]):
                    await session.initialize()
                    result = await session.call_tool("graph_query", {})
                    return result.isError

    # graph_query 缺 query_type -> 函数签名错误 -> MCPServer 返回 isError
    assert _run(_call()) is True