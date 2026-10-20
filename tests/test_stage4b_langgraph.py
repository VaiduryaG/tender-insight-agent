"""LangGraph 版 Agent 测试（FakeLLM 替身，零 API 消耗）。

覆盖：工具循环、幻觉防御条件边、消息角色映射（langchain type → OpenAI role）、
非必填工具参数、与手写版 AgentResult 结构兼容。
运行: ./run.sh -m pytest tests/test_stage4b_langgraph.py -v
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agent.conversation import Conversation
from src.agent.graph_agent import LangGraphAgent, _lc_to_dict, registry_to_base_tools
from src.agent.prompts import SYSTEM_PROMPT, SIMULATED_ANSWER_NUDGE
from src.mcp_tools.registry import ToolRegistry


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.register(
        name="add",
        description="求和",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    )
    def add(a, b):
        return {"sum": a + b}

    @reg.register(
        name="lookup",
        description="带非必填参数的查询",
        parameters={
            "type": "object",
            "properties": {
                "query_type": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["query_type"],  # name 非必填
        },
    )
    def lookup(query_type, name=None):
        return {"query_type": query_type, "name": name}

    return reg


class FakeLLM:
    """脚本化替身：按序返回预设消息（与 test_stage4 的手写版替身同构）。"""

    def __init__(self, scripted: list[dict]):
        self.scripted = list(scripted)
        self.received: list[list[dict]] = []

    def chat(self, messages: list[dict], tools=None, **kw) -> dict:
        self.received.append([dict(m) for m in messages])
        return self.scripted.pop(0)


def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


# ---------------------------------------------------------------------------
# 消息角色映射（智谱 400 "角色信息不正确" 回归）
# ---------------------------------------------------------------------------


def test_lc_to_dict_maps_langchain_roles_to_openai():
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    assert _lc_to_dict(SystemMessage(content="s"))["role"] == "system"
    assert _lc_to_dict(HumanMessage(content="q"))["role"] == "user"  # 不是 "human"
    assert _lc_to_dict(AIMessage(content="a"))["role"] == "assistant"  # 不是 "ai"
    tm = ToolMessage(content="obs", tool_call_id="t1")
    assert _lc_to_dict(tm) == {"role": "tool", "tool_call_id": "t1", "content": "obs"}


def test_lc_to_dict_preserves_tool_calls():
    from langchain_core.messages import AIMessage

    ai = AIMessage(content="", tool_calls=[{"name": "add", "args": {"a": 1, "b": 2}, "id": "c1", "type": "tool_call"}])
    d = _lc_to_dict(ai)
    assert d["role"] == "assistant"
    assert d["tool_calls"][0]["function"]["name"] == "add"
    assert json.loads(d["tool_calls"][0]["function"]["arguments"]) == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# registry → BaseTool 适配
# ---------------------------------------------------------------------------


def test_registry_to_base_tools_respects_required(registry):
    tools = {t.name: t for t in registry_to_base_tools(registry)}
    # add 的 a/b 必填；lookup 只有 query_type 必填（name 可选）
    assert set(tools["add"].args_schema.model_json_schema().get("required", [])) == {"a", "b"}
    assert set(tools["lookup"].args_schema.model_json_schema().get("required", [])) == {"query_type"}


def test_registry_to_base_tools_delegates_execution(registry):
    tools = {t.name: t for t in registry_to_base_tools(registry)}
    result = json.loads(tools["add"].invoke({"a": 2, "b": 3}))
    assert result == {"sum": 5}


# ---------------------------------------------------------------------------
# LangGraphAgent 完整循环（FakeLLM）
# ---------------------------------------------------------------------------


def test_langgraph_agent_tool_loop(registry):
    fake = FakeLLM([
        {"content": "我来算", "tool_calls": [_tool_call("c1", "add", {"a": 1, "b": 2})]},
        {"content": "结果是 3", "tool_calls": None},
    ])
    agent = LangGraphAgent(fake, registry, max_steps=8)
    r = agent.run("1+2等于几")
    assert r.answer == "结果是 3"
    assert r.used_tools is True
    assert r.retried_for_simulation is False
    assert r.steps == 2
    # trace 结构与手写版对齐
    assert r.trace[0]["tool_calls"][0]["name"] == "add"
    assert "3" in r.trace[0]["observation"]


def test_langgraph_agent_simulation_defense(registry):
    """第一轮文本模拟工具执行 → 条件边触发 nudge → 第二轮正常回答。"""
    fake = FakeLLM([
        {"content": "Thought: 算\nAction: add\nObservation: 3\n最终答案：3", "tool_calls": None},
        {"content": "真实计算结果 3", "tool_calls": None},
    ])
    agent = LangGraphAgent(fake, registry, max_steps=8)
    r = agent.run("1+2等于几")
    assert r.answer == "真实计算结果 3"
    assert r.retried_for_simulation is True
    # 重试轮应包含 SIMULATED_ANSWER_NUDGE 消息
    last_round = fake.received[-1]
    assert any(m.get("content") == SIMULATED_ANSWER_NUDGE for m in last_round)


def test_langgraph_agent_simulated_answer_with_tools_not_retried(registry):
    """调用了工具的回答即使含协议词也不触发重试（与手写版行为一致）。"""
    fake = FakeLLM([
        {"content": "计算", "tool_calls": [_tool_call("c1", "add", {"a": 1, "b": 2})]},
        {"content": "Observation 显示和为 3，最终答案：3", "tool_calls": None},
    ])
    agent = LangGraphAgent(fake, registry, max_steps=8)
    r = agent.run("1+2等于几")
    assert r.answer == "Observation 显示和为 3，最终答案：3"
    assert r.retried_for_simulation is False
    assert r.used_tools is True


def test_langgraph_agent_direct_answer(registry):
    """无工具直答路径。"""
    fake = FakeLLM([{"content": "直接回答", "tool_calls": None}])
    agent = LangGraphAgent(fake, registry, max_steps=8)
    r = agent.run("你好")
    assert r.answer == "直接回答"
    assert r.used_tools is False


def test_langgraph_agent_history_and_system(registry):
    """system 提示词注入 + 历史消息传递。"""
    fake = FakeLLM([{"content": "ok", "tool_calls": None}])
    agent = LangGraphAgent(fake, registry, max_steps=8)
    hist = Conversation(max_messages=10)
    hist.add_exchange(" earlier", " earlier answer")
    agent.run("新问题", history=hist)
    first_round = fake.received[0]
    roles = [m["role"] for m in first_round]
    assert roles[0] == "system"
    assert roles[1] == "user" and roles[2] == "assistant"  # 历史
    assert roles[-1] == "user"
    assert first_round[0]["content"] == SYSTEM_PROMPT


def test_langgraph_agent_non_required_tool_param(registry):
    """模型只传必填参数时工具正常执行（pydantic 校验回归）。"""
    fake = FakeLLM([
        {"content": "查", "tool_calls": [_tool_call("c1", "lookup", {"query_type": "region"})]},
        {"content": "查到了", "tool_calls": None},
    ])
    agent = LangGraphAgent(fake, registry, max_steps=8)
    r = agent.run("查一下")
    assert r.used_tools is True
    assert "查到了" == r.answer


def test_langgraph_agent_omitted_optional_param_not_none():
    """IT-022 回归：模型省略的可选参数不能以显式 None 传给工具。

    pydantic 参数模型会把省略字段填成 None；若原样透传 registry，工具收到的
    是 top_k=None（Python 默认值失效）。正确行为：None 键被剔除，工具收到
    注册函数签名里的默认值 top_k=10。
    """
    reg = ToolRegistry()

    @reg.register(
        name="probe",
        description="记录实际收到的参数",
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string"}, "top_k": {"type": "integer"}},
            "required": ["q"],
        },
    )
    def probe(q, top_k=10):
        return {"q": q, "top_k": top_k}

    fake = FakeLLM([
        {"content": "查", "tool_calls": [_tool_call("c1", "probe", {"q": "校园"})]},
        {"content": "ok", "tool_calls": None},
    ])
    agent = LangGraphAgent(fake, reg, max_steps=8)
    r = agent.run("查")

    observation = r.trace[0]["observation"]  # 工具返回: {"q": "校园", "top_k": 10}
    assert '"top_k": 10' in observation  # 默认值生效（None 透传则会是 "top_k": null）
