"""查询改写（Query Rewrite）测试：输出解析 / best-effort 降级 / Agent 集成。

运行: ./run.sh -m pytest tests/test_query_rewrite.py -v
"""

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agent.query_rewrite import _parse_keywords, rewrite_query
from src.mcp_tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# _parse_keywords：LLM 输出的鲁棒解析
# ---------------------------------------------------------------------------


def test_parse_strips_numbering_and_punctuation():
    content = "1. 学校\n2. 器材\n3. 设备采购\n、校园\n- 教学设备"
    assert _parse_keywords(content, 6) == ["学校", "器材", "设备采购", "校园", "教学设备"]


def test_parse_dedup_and_length_filter():
    content = "学校\n学校\n设\n这是一句完整的话远远超过十二个字的长度限制\n ok "
    # 重复去重；"设"单字过滤；>12字长句过滤；"ok"两字保留（长度过滤只拦单字）
    assert _parse_keywords(content, 6) == ["学校", "ok"]


def test_parse_max_keywords_cap():
    content = "\n".join(f"关键词{i}" for i in range(10))
    assert len(_parse_keywords(content, 4)) == 4


# ---------------------------------------------------------------------------
# rewrite_query：best-effort，任何失败返回 []
# ---------------------------------------------------------------------------


class _FakeLLM:
    """脚本化替身：按序返回 content 或抛异常。"""

    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.received: list[list[dict]] = []

    def chat(self, messages: list[dict], tools=None, **kw: Any) -> dict:
        self.received.append([dict(m) for m in messages])
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"content": item, "tool_calls": None}


def test_rewrite_success():
    llm = _FakeLLM(["学校\n器材\n教学设备"])
    assert rewrite_query(llm, "学校要购置一批器材") == ["学校", "器材", "教学设备"]
    # prompt 中包含用户问题
    assert "学校要购置一批器材" in llm.received[0][0]["content"]


def test_rewrite_llm_exception_returns_empty():
    llm = _FakeLLM([RuntimeError("API 挂了")])
    assert rewrite_query(llm, "学校器材") == []


def test_rewrite_empty_or_none_content_returns_empty():
    assert rewrite_query(_FakeLLM([""]), "学校器材") == []
    assert rewrite_query(_FakeLLM(["   "]), "学校器材") == []


def test_rewrite_empty_question_skips_llm():
    llm = _FakeLLM(["不该被消费"])
    assert rewrite_query(llm, "  ") == []
    assert llm.received == []  # 没有产生任何 LLM 调用


# ---------------------------------------------------------------------------
# Agent 集成：改写关键词注入 user 消息
# ---------------------------------------------------------------------------


def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


class _AgentFakeLLM:
    """脚本化替身（与 test_stage4b 同构）：首轮回 tool_calls，次轮直答。"""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.received: list[list[dict]] = []

    def chat(self, messages: list[dict], tools=None, **kw: Any) -> dict:
        self.received.append([dict(m) for m in messages])
        return self.script.pop(0)


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.register(
        name="probe",
        description="探测参数",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
    )
    def probe(q):
        return {"q": q}

    return reg


def test_agent_injects_rewritten_keywords(registry):
    """改写成功 → user 消息携带检索提示；问题原句保留。"""
    from src.agent.graph_agent import LangGraphAgent

    fake = _AgentFakeLLM([
        {"content": "查", "tool_calls": [_tool_call("c1", "probe", {"q": "学校"})]},
        {"content": "答毕", "tool_calls": None},
    ])
    agent = LangGraphAgent(
        fake, registry, max_steps=4,
        query_rewriter=lambda q: ["学校", "器材"],
    )
    agent.run("学校要购置一批器材")

    user_msg = next(m for m in fake.received[0] if m["role"] == "user")
    assert "学校要购置一批器材" in user_msg["content"]          # 原句保留
    assert "学校、器材" in user_msg["content"]                  # 改写关键词注入
    assert "[检索提示]" in user_msg["content"]


def test_agent_skips_hint_when_rewriter_returns_empty(registry):
    """改写失败/返回空 → user 消息就是原句，不带提示块。"""
    from src.agent.graph_agent import LangGraphAgent

    fake = _AgentFakeLLM([
        {"content": "查", "tool_calls": [_tool_call("c1", "probe", {"q": "x"})]},
        {"content": "答毕", "tool_calls": None},
    ])
    agent = LangGraphAgent(fake, registry, max_steps=4, query_rewriter=lambda q: [])
    agent.run("学校器材")

    user_msg = next(m for m in fake.received[0] if m["role"] == "user")
    assert user_msg["content"] == "学校器材"


def test_agent_without_rewriter_unchanged(registry):
    """不注入 rewriter（最简部署）→ 行为与旧版完全一致。"""
    from src.agent.graph_agent import LangGraphAgent

    fake = _AgentFakeLLM([
        {"content": "查", "tool_calls": [_tool_call("c1", "probe", {"q": "x"})]},
        {"content": "答毕", "tool_calls": None},
    ])
    agent = LangGraphAgent(fake, registry, max_steps=4)
    agent.run("学校器材")

    user_msg = next(m for m in fake.received[0] if m["role"] == "user")
    assert user_msg["content"] == "学校器材"
