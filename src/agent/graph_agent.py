"""LangGraph 版 ReAct Agent —— 与手写版 (react.py) 行为对齐的框架实现。

架构（StateGraph 条件边循环）：

    START → agent（LLM 推理，可能产出 tool_calls）
              │
              ├─ 有 tool_calls → tools（ToolNode 批量执行）→ 回到 agent
              │
              └─ 无 tool_calls（最终回答）→ route_after_agent 条件边判定：
                    ├─ 回答带"模拟工具执行"特征且未真正调用过工具
                    │    → nudge 节点（注入纠正消息）→ 回到 agent（幻觉防御，IT-014）
                    └─ 正常 → END

与手写版的对应关系（学习报告 langchain_langgraph_study.md 第四节的落地版）：
    ReActAgent._run_loop   → StateGraph 循环 + should_continue 条件边
    幻觉检测重试（run 尾部）→ nudge 条件边（框架原生写法）
    registry.schemas()     → ToolNode 自动把 JSON Schema 转成 bind_tools 参数
    Conversation 裁剪历史  → 仍复用 Conversation（run() 拼消息后整段交给图）

对外接口与 ReActAgent 完全一致：run(question, history) -> AgentResult，
Web 层 / run_agent.py / 测试可无缝互换（见 common.load_agent 的开关）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Annotated, Any

from langchain_core.messages import (
    AIMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from src.agent.conversation import Conversation
from src.agent.prompts import SIMULATED_ANSWER_NUDGE, SYSTEM_PROMPT
from src.agent.react import AgentResult, looks_simulated
from src.agent.protocols import SupportsChat
from src.mcp_tools.registry import ToolRegistry


# ---- 把自研 ToolRegistry 适配为 langchain 工具 --------------------------------

def registry_to_base_tools(registry: ToolRegistry) -> list:
    """把 ToolRegistry 的每个工具包成 langchain BaseTool。

    实现不复制：BaseTool._run 统一委托 registry.call(name, args_json)，
    tools.py 仍是唯一的工具逻辑所在地。参数 schema 从 JSON Schema 动态
    生成 pydantic 模型（langgraph ToolNode 需要 BaseTool 而非裸 dict）。
    """
    from pydantic import BaseModel, Field, create_model

    from langchain_core.tools import BaseTool

    JSON_TYPE_MAP = {"string": (str, ...), "number": (float, ...), "integer": (int, ...),
                     "boolean": (bool, ...), "array": (list, ...), "object": (dict, ...)}

    def _make_args_model(name: str, schema: dict) -> type[BaseModel]:
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        fields = {}
        for pname, p in props.items():
            ann, _ = JSON_TYPE_MAP.get(p.get("type", "string"), (str, ...))
            if pname in required:
                fields[pname] = (ann, Field(..., description=p.get("description", "")))
            else:
                # 非必填字段给默认 None，避免 pydantic 把它们全标成 required
                fields[pname] = (ann | None, Field(default=None, description=p.get("description", "")))
        return create_model(f"{name}_args", **fields)

    class _RegistryTool(BaseTool):
        # 每个工具动态子类化（name/args_schema 在创建时确定）
        pass

    tools = []
    for t in registry.tools.values():
        args_model = _make_args_model(t.name, t.parameters)

        def _run(self, **kwargs):
            return self._registry.call(self._tool_name, json.dumps(kwargs, ensure_ascii=False))

        # pydantic 要求：覆盖/新增的字段必须带注解（含下划线私有属性）
        ns: dict = {
            "__module__": __name__,
            "__annotations__": {
                "name": str,
                "description": str,
                "args_schema": type[BaseModel],
                "_registry": ToolRegistry,
                "_tool_name": str,
            },
            "name": t.name,
            "description": t.description,
            "args_schema": args_model,
            "_registry": registry,
            "_tool_name": t.name,
            "_run": _run,
        }
        tool_cls = type(f"_Tool_{t.name}", (BaseTool,), ns)
        tools.append(tool_cls())
    return tools


# ---- 状态定义 -------------------------------------------------------------------

class AgentState(dict):
    """图状态：messages 由 add_messages 合并（append 语义，自动去重 id）。"""

    messages: Annotated[list, add_messages]
    used_tools: bool            # 是否真正调用过工具（幻觉防御判据）
    retried: bool               # 是否已触发过纠正重试（只重试一次）


# ---- LangGraph Agent -------------------------------------------------------------

class LangGraphAgent:
    """与 ReActAgent 同接口的 LangGraph 实现。

    llm 参数接受 SupportsChat（自研协议）以复用测试替身；真实运行传
    LLMClient 即可——两者都暴露 chat(messages, tools) -> dict，
    由 _ChatAdapter 包装成 langchain 的 Runnable。
    """

    def __init__(
        self,
        llm: SupportsChat,
        registry: ToolRegistry,
        max_steps: int = 8,
        system_prompt: str = SYSTEM_PROMPT,
        verbose: bool = False,
    ):
        self.llm = llm
        self.registry = registry
        self.max_steps = max_steps
        self.system_prompt = system_prompt
        self.verbose = verbose

        from langchain_core.runnables import RunnableLambda

        self._model = RunnableLambda(self._chat_via_protocol)  # dict 协议 → AIMessage
        self._tools = ToolNode(registry_to_base_tools(registry))

        self._graph = self._build_graph()

    # ---- LLM 适配：复用 SupportsChat 协议（真实 LLMClient / FakeLLM 通吃） ----

    def _chat_via_protocol(self, messages: list) -> AIMessage:
        """把 langchain 消息列表转回 dict 格式调 SupportsChat.chat，再包成 AIMessage。"""
        dict_msgs = [_lc_to_dict(m) for m in messages]
        msg = self.llm.chat(dict_msgs, tools=self.registry.schemas())
        content = msg.get("content")
        tool_calls = [
            {
                "name": tc["function"]["name"],
                "args": json.loads(tc["function"].get("arguments") or "{}"),
                "id": tc["id"],
                "type": "tool_call",
            }
            for tc in (msg.get("tool_calls") or [])
        ]
        return AIMessage(content=content or "", tool_calls=tool_calls)

    # ---- 图构建 ---------------------------------------------------------------

    def _build_graph(self):
        g = StateGraph(AgentState)

        def agent_node(state: AgentState) -> dict:
            # 系统提示词在图内注入（保证重试轮次也带 system）
            msgs = state["messages"]
            if not (msgs and isinstance(msgs[0], SystemMessage)):
                msgs = [SystemMessage(content=self.system_prompt), *msgs]
            ai = self._model.invoke(msgs)
            return {"messages": [ai]}

        def tools_node(state: AgentState) -> dict:
            result = self._tools.invoke(state)
            return {"messages": result["messages"], "used_tools": True}

        def nudge_node(state: AgentState) -> dict:
            """幻觉纠正：把带模拟特征的回答与纠正消息追加进对话。"""
            last = state["messages"][-1]
            return {
                "messages": [
                    last,  # 保留被判定为模拟的回答，作为纠正上下文
                    {"role": "user", "content": SIMULATED_ANSWER_NUDGE},
                ],
                "retried": True,
            }

        def route_after_agent(state: AgentState) -> str:
            last = state["messages"][-1]
            if isinstance(last, AIMessage) and last.tool_calls:
                return "tools"
            # 最终回答：幻觉防御条件边
            answer = last.content if isinstance(last.content, str) else str(last.content)
            if looks_simulated(answer) and not state.get("used_tools") and not state.get("retried"):
                return "nudge"
            return END

        g.add_node("agent", agent_node)
        g.add_node("tools", tools_node)
        g.add_node("nudge", nudge_node)
        g.add_edge(START, "agent")
        g.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "nudge": "nudge", END: END})
        g.add_edge("tools", "agent")
        g.add_edge("nudge", "agent")
        return g.compile()

    # ---- 对外接口：与 ReActAgent.run 完全一致 ----------------------------------

    def run(self, question: str, history: Conversation | None = None) -> AgentResult:
        messages: list = [SystemMessage(content=self.system_prompt)]
        if history:
            for m in history.as_list():
                messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": question})

        final_state = self._graph.invoke(
            {"messages": messages, "used_tools": False, "retried": False},
            config={"recursion_limit": self.max_steps * 2 + 6},
        )

        answer, trace, used_tools = self._extract(final_state["messages"])
        return AgentResult(
            answer=answer,
            trace=trace,
            steps=len([m for m in final_state["messages"] if isinstance(m, AIMessage)]),
            used_tools=used_tools,
            retried_for_simulation=bool(final_state.get("retried")),
        )

    # ---- trace 提取：与手写版 trace_summary 的数据结构对齐 ----------------------

    def _extract(self, messages: list) -> tuple[str, list[dict[str, Any]], bool]:
        """把消息序列还原成手写版 trace 结构（thought/tool_calls/observation）。"""
        trace: list[dict[str, Any]] = []
        used_tools = False
        answer = ""
        for m in messages:
            if isinstance(m, AIMessage):
                tcs = [
                    {"name": tc["name"], "arguments": json.dumps(tc["args"], ensure_ascii=False)}
                    for tc in (m.tool_calls or [])
                ]
                if tcs:
                    trace.append({"thought": m.content, "tool_calls": tcs, "observation": ""})
                else:
                    answer = m.content if isinstance(m.content, str) else str(m.content)
                    trace.append({"thought": m.content, "tool_calls": [], "observation": None})
            elif isinstance(m, ToolMessage):
                used_tools = True
                if trace:
                    prev = trace[-1]
                    obs = f"工具返回: {str(m.content)[:200]}"
                    prev["observation"] = (prev["observation"] + "\n" + obs).strip() if prev["observation"] else obs
        return answer, trace, used_tools


def _lc_to_dict(m) -> dict:
    """langchain 消息 → SupportsChat 协议的 dict（OpenAI role 命名）。

    注意：langchain 的 m.type 是 'human'/'ai'/'tool'，而 OpenAI 兼容 API
    要求 'user'/'assistant'/'tool'——直接透传 type 会触发智谱 400
    '角色信息不正确'（错误码 1214）。必须显式映射。
    """
    if isinstance(m, dict):
        return m
    if isinstance(m, SystemMessage):
        return {"role": "system", "content": m.content}
    if isinstance(m, ToolMessage):
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    if isinstance(m, AIMessage):
        d: dict = {"role": "assistant", "content": m.content if isinstance(m.content, str) else str(m.content)}
        if m.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["args"], ensure_ascii=False)},
                }
                for tc in m.tool_calls
            ]
        return d
    # HumanMessage 及其他 → user
    content = m.content if isinstance(m.content, str) else str(m.content)
    return {"role": "user", "content": content}
