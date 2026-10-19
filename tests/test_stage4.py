"""阶段4 Agent核心测试（LLM 用测试替身，不依赖网络与API）。

运行: ./run.sh -m pytest tests/test_stage4.py -v
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from src.agent.conversation import Conversation
from src.agent.protocols import SupportsChat
from src.agent.prompts import SIMULATED_ANSWER_NUDGE
from src.agent.react import ReActAgent, looks_simulated
from src.knowledge_graph.builder import build_graph
from src.knowledge_graph.store import NetworkXStore
from src.mcp_tools import tools as tools_mod
from src.mcp_tools.registry import ToolRegistry
from src.mcp_tools.tools import build_registry
from src.vector_search.embedder import HashingEmbedder
from src.vector_search.search import ProjectVectorIndex


# ---------------------------------------------------------------------------
# 测试替身 LLM
# ---------------------------------------------------------------------------


class FakeLLM:
    """脚本化替身：按序返回预设消息（支持 content / tool_calls）。"""

    def __init__(self, scripted: list[dict]):
        self.scripted = list(scripted)
        self.received: list[list[dict]] = []

    def chat(self, messages: list[dict], tools=None, **kw: Any) -> dict:
        self.received.append([dict(m) for m in messages])
        return self.scripted.pop(0)


def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.register(name="add", description="求和", parameters={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]})
    def add(a, b):
        return {"sum": a + b}

    @reg.register(name="boom", description="必失败", parameters={"type": "object", "properties": {}})
    def boom():
        raise ValueError("炸了")

    return reg


def test_registry_schemas(registry):
    schemas = registry.schemas()
    assert [s["function"]["name"] for s in schemas] == ["add", "boom"]


def test_registry_call_ok(registry):
    assert json.loads(registry.call("add", {"a": 1, "b": 2})) == {"sum": 3}
    assert json.loads(registry.call("add", '{"a": 1, "b": 2}')) == {"sum": 3}  # JSON字符串参数


def test_registry_call_error_paths(registry):
    assert "未知工具" in registry.call("nope", {})
    assert "合法JSON" in registry.call("add", "{bad json")
    assert "参数不匹配" in registry.call("add", {"a": 1})  # 缺 b
    assert "工具执行失败" in registry.call("boom", {})


def test_registry_result_truncation():
    reg = ToolRegistry()

    @reg.register(name="big", description="", parameters={"type": "object", "properties": {}})
    def big():
        return {"data": "x" * 9999}

    out = reg.call("big", {})
    assert len(out) <= 4100 and "截断" in out


# ---------------------------------------------------------------------------
# ReActAgent 循环
# ---------------------------------------------------------------------------


def test_agent_runs_tool_then_answers(registry):
    llm = FakeLLM(
        [
            {"content": "需要计算 1+2", "tool_calls": [_tool_call("c1", "add", {"a": 1, "b": 2})]},
            {"content": "1+2=3", "tool_calls": None},
        ]
    )
    agent = ReActAgent(llm, registry, max_steps=4)
    result = agent.run("1加2等于几？")

    assert result.answer == "1+2=3"
    assert result.steps == 2
    assert result.trace[0]["thought"] == "需要计算 1+2"
    assert result.trace[0]["tool_calls"][0]["name"] == "add"
    assert '"sum": 3' in result.trace[0]["observation"]
    # 观察结果作为 tool 消息回填，且带 tool_call_id
    tool_msgs = [m for m in llm.received[1] if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c1"


def test_agent_multiple_tools_one_step(registry):
    llm = FakeLLM(
        [
            {"content": None, "tool_calls": [
                _tool_call("c1", "add", {"a": 1, "b": 1}),
                _tool_call("c2", "add", {"a": 2, "b": 2}),
            ]},
            {"content": "两步结果 2 和 4", "tool_calls": None},
        ]
    )
    result = ReActAgent(llm, registry).run("并行调用")
    assert len(result.trace[0]["tool_calls"]) == 2
    assert "2" in result.trace[0]["observation"] and "4" in result.trace[0]["observation"]


def test_agent_max_steps_fallback(registry):
    # max_steps=3: 循环内 chat 3 次 + 强制收尾 chat 1 次 = 共 4 条脚本
    scripted = [
        {"content": f"继续第{i}步", "tool_calls": [_tool_call(f"c{i}", "add", {"a": i, "b": 0})]}
        for i in range(3)
    ]
    scripted.append({"content": "最终答案", "tool_calls": None})  # 收尾调用 tools=None
    llm = FakeLLM(scripted)
    result = ReActAgent(llm, registry, max_steps=3).run("无限循环题")

    assert result.steps == 3
    assert result.answer == "最终答案"
    assert result.trace[-1]["observation"]


def test_agent_direct_answer_without_tools(registry):
    llm = FakeLLM([{"content": "不需要工具", "tool_calls": None}])
    result = ReActAgent(llm, registry).run("你好")
    assert result.answer == "不需要工具" and result.steps == 1
    assert result.used_tools is False


# ---------------------------------------------------------------------------
# 幻觉防御（IT-014 回归用例）：模拟式回答检测 + 自动纠正重试
# ---------------------------------------------------------------------------


SIMULATED_ANSWER = (
    "Thought: 首先需要查询智慧教育行业金额最高的公司。\n\n"
    "Action: 使用 graph_query 工具查询。\n\n"
    "Observation: 智慧教育行业金额最高的公司是XX公司。\n\n"
    "最终答案：XX公司金额最高，竞争对手有YY公司、ZZ公司。"
)


def test_looks_simulated_detector():
    assert looks_simulated(SIMULATED_ANSWER)
    assert looks_simulated("最终答案：XX公司")
    assert not looks_simulated("智慧教育行业金额最高的是甲公司有限公司，共100万元。")
    assert not looks_simulated("")


def test_agent_detects_and_retries_simulated_answer(registry):
    """第一轮返回编造的模拟式回答（无 tool_calls）→ 注入纠正消息重试 → 第二轮走真实工具。"""
    llm = FakeLLM(
        [
            {"content": SIMULATED_ANSWER, "tool_calls": None},  # 幻觉轮
            {"content": "需要查询真实数据", "tool_calls": [_tool_call("r1", "add", {"a": 1, "b": 1})]},
            {"content": "真实答案是2", "tool_calls": None},
        ]
    )
    result = ReActAgent(llm, registry, max_steps=4).run("1加2等于几？")

    assert result.retried_for_simulation is True
    assert result.used_tools is True
    assert result.answer == "真实答案是2"
    # 重试轮的消息结构：幻觉回答作为 assistant 注入 + SIMULATED_ANSWER_NUDGE 纠正消息
    retry_round = llm.received[1]
    assert retry_round[-2]["role"] == "assistant"
    assert SIMULATED_ANSWER in retry_round[-2]["content"]
    assert retry_round[-1] == {"role": "user", "content": SIMULATED_ANSWER_NUDGE}


def test_agent_retry_once_then_accept(registry):
    """重试后仍是模拟式回答 → 接受结果（不无限重试），标记保持。"""
    llm = FakeLLM(
        [
            {"content": SIMULATED_ANSWER, "tool_calls": None},
            {"content": "第二次 Thought: 还是编造", "tool_calls": None},
        ]
    )
    result = ReActAgent(llm, registry, max_steps=4).run("问题")
    assert result.retried_for_simulation is True
    assert result.used_tools is False
    assert "第二次" in result.answer


def test_agent_used_tools_flag_normal_path(registry):
    llm = FakeLLM(
        [
            {"content": "算一下", "tool_calls": [_tool_call("c1", "add", {"a": 2, "b": 2})]},
            {"content": "答案是4", "tool_calls": None},
        ]
    )
    result = ReActAgent(llm, registry).run("2加2")
    assert result.used_tools is True and result.retried_for_simulation is False


def test_simulated_answer_with_tools_not_retried(registry):
    """虽然 content 带模拟字样，但真正调用了工具 → 不触发重试（模型边调边写草稿是正常行为）。"""
    llm = FakeLLM(
        [
            {"content": "Thought: 我先查一下", "tool_calls": [_tool_call("c1", "add", {"a": 1, "b": 1})]},
            {"content": "答案是2", "tool_calls": None},
        ]
    )
    result = ReActAgent(llm, registry).run("1加1")
    assert result.retried_for_simulation is False and result.answer == "答案是2"


def test_agent_system_prompt_injected(registry):
    llm = FakeLLM([{"content": "ok", "tool_calls": None}])
    ReActAgent(llm, registry).run("问题")
    assert llm.received[0][0]["role"] == "system"
    assert llm.received[0][-1] == {"role": "user", "content": "问题"}


def test_llm_client_satisfies_protocol():
    """真实 LLMClient 与 FakeLLM 均满足 chat 协议（可互换）。"""
    assert isinstance(FakeLLM([]), SupportsChat)
    from src.agent.llm import LLMClient

    assert isinstance(LLMClient(api_key="dummy"), SupportsChat)


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


def test_conversation_trim_keeps_pairs():
    conv = Conversation(max_messages=4)
    for i in range(5):
        conv.add_exchange(f"问题{i}", f"回答{i}")
    roles = [m["role"] for m in conv.messages]
    assert len(conv.messages) == 4
    assert roles == ["user", "assistant", "user", "assistant"]  # 偶数对齐


# ---------------------------------------------------------------------------
# 工具集成测试（mini 数据 + monkeypatch 缓存）
# ---------------------------------------------------------------------------


def _mini_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "title": "甲公司中标校园项目", "title_normalized": "甲公司中标校园项目",
                "company_name": "甲公司有限公司", "canonical_name": "甲公司有限公司",
                "group_name": "甲集团", "amount_normalized": 100.0,
                "announcement_date_normalized": "2026-09-01", "project_id": "P1",
                "industry": "智慧教育", "province": "山东", "city": "济南",
                "tech_tags_list": ["AI"], "summary_normalized": "校园信息化",
            },
            {
                "title": "乙公司中标交通项目", "title_normalized": "乙公司中标交通项目",
                "company_name": "乙公司股份有限公司", "canonical_name": "乙公司股份有限公司",
                "group_name": "乙集团", "amount_normalized": 900.0,
                "announcement_date_normalized": "2026-09-02", "project_id": "P2",
                "industry": "智能交通", "province": "北京", "city": "北京",
                "tech_tags_list": ["物联网"], "summary_normalized": "交通管控",
            },
        ]
    )


@pytest.fixture()
def tool_registry(monkeypatch, tmp_path) -> ToolRegistry:
    """注入 mini 数据到工具层缓存，隔离真实数据文件。"""
    store = NetworkXStore()
    build_graph(_mini_df(), store)

    idx = ProjectVectorIndex(HashingEmbedder(), db_path=tmp_path / "chroma")
    idx.build(_mini_df())

    monkeypatch.setattr(tools_mod, "_cache", {"df": _mini_df(), "graph": store, "index": idx})
    return build_registry()


def test_tool_search_projects_filter(tool_registry):
    r = json.loads(tool_registry.call("search_projects", {"industry": "智慧教育"}))
    assert r["total_matched"] == 1 and r["projects"][0]["company"] == "甲公司有限公司"
    r = json.loads(tool_registry.call("search_projects", {"min_amount": 500}))
    assert r["projects"][0]["group"] == "乙集团"


def test_tool_company_profile_group_aggregation(tool_registry):
    """集团名提问聚合成员公司；分公司名精确命中。"""
    r = json.loads(tool_registry.call("company_profile", {"company": "甲集团"}))
    assert r["exists"] and r["project_count"] == 1 and r["total_amount"] == 100.0
    r = json.loads(tool_registry.call("company_profile", {"company": "甲公司有限公司"}))
    assert r["exists"] and r["group"] == "甲集团"
    r = json.loads(tool_registry.call("company_profile", {"company": "不存在的公司"}))
    assert not r["exists"]


def test_tool_graph_query_dispatch(tool_registry):
    r = json.loads(tool_registry.call("graph_query", {"query_type": "tech_trends"}))
    assert {t["technology"] for t in r["trends"]} == {"AI", "物联网"}
    r = json.loads(tool_registry.call("graph_query", {"query_type": "competitors"}))
    assert "需要提供 company" in r["error"]


def test_tool_industry_stats(tool_registry):
    r = json.loads(tool_registry.call("industry_stats", {"group_by": "industry"}))
    assert r["stats"][0]["name"] == "智能交通"  # 按金额降序
    r = json.loads(tool_registry.call("industry_stats", {"group_by": "bad"}))
    assert "error" in r


def test_tool_semantic_search(tool_registry):
    r = json.loads(tool_registry.call("semantic_search", {"query": "校园信息化"}))
    assert r["results"][0]["company"] == "甲公司有限公司"


# ---------------------------------------------------------------------------
# LLMClient 重试策略测试（IT-017）
# ---------------------------------------------------------------------------


def _mock_response(status_code: int) -> SimpleNamespace:
    """创建模拟 httpx2.Response 对象（带 request 和 headers 属性）。"""
    request = SimpleNamespace(method="POST", url="https://api.test.com/v1/chat/completions")
    headers = {"x-request-id": "test-request-id"}
    return SimpleNamespace(status_code=status_code, request=request, headers=headers)


def test_llm_non_retryable_error_raises_immediately():
    """4xx 客户端错误立即抛出，不重试。"""
    from unittest.mock import patch

    from openai import BadRequestError

    from src.agent.llm import LLMClient

    client = LLMClient(api_key="test")

    with patch.object(client, "_get_client") as mock_get_client:
        # 模拟 BadRequestError
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = BadRequestError(
            message="Invalid request: role must be user or assistant",
            response=_mock_response(400),
            body={"error": {"code": 1214, "message": "角色信息不正确"}},
        )

        # 应该立即抛出，不重试
        with pytest.raises(BadRequestError) as exc_info:
            client.chat([{"role": "user", "content": "test"}])

        # 验证只调用了一次（没有重试）
        assert mock_client.chat.completions.create.call_count == 1


def test_llm_retryable_error_retries():
    """网络异常/限流/5xx 应该重试。"""
    from unittest.mock import patch

    from openai import APIConnectionError

    from src.agent.llm import LLMClient

    client = LLMClient(api_key="test")

    with patch.object(client, "_get_client") as mock_get_client:
        mock_client = mock_get_client.return_value
        # 前两次失败，第三次成功
        mock_request = SimpleNamespace(method="POST", url="https://api.test.com/v1/chat/completions")
        mock_client.chat.completions.create.side_effect = [
            APIConnectionError(message="Connection failed", request=mock_request),
            APIConnectionError(message="Connection failed again", request=mock_request),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="success", tool_calls=None)
                    )
                ]
            ),
        ]

        result = client.chat([{"role": "user", "content": "test"}])
        assert result["content"] == "success"
        # 验证重试了3次
        assert mock_client.chat.completions.create.call_count == 3


def test_llm_retryable_error_fails_after_max_retries():
    """可重试错误连续3次失败后抛出 RuntimeError。"""
    from unittest.mock import patch

    from openai import RateLimitError

    from src.agent.llm import LLMClient

    client = LLMClient(api_key="test")

    with patch.object(client, "_get_client") as mock_get_client:
        mock_client = mock_get_client.return_value
        # 连续失败3次
        mock_client.chat.completions.create.side_effect = RateLimitError(
            message="Rate limit exceeded",
            response=_mock_response(429),
            body={"error": {"message": "Rate limit"}},
        )

        with pytest.raises(RuntimeError, match="连续 3 次失败"):
            client.chat([{"role": "user", "content": "test"}])

        assert mock_client.chat.completions.create.call_count == 3


def test_llm_unknown_exception_not_retried():
    """未知异常立即抛出，不重试。"""
    from unittest.mock import patch

    from src.agent.llm import LLMClient

    client = LLMClient(api_key="test")

    with patch.object(client, "_get_client") as mock_get_client:
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.side_effect = ValueError("Unexpected error")

        with pytest.raises(ValueError, match="Unexpected error"):
            client.chat([{"role": "user", "content": "test"}])

        # 只调用一次，没有重试
        assert mock_client.chat.completions.create.call_count == 1
