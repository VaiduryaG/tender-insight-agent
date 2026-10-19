"""ReAct Agent 核心循环。

Thought → Action(function calling) → Observation(tool 消息回填) → … → Final Answer。
每一步都记录到 trace（含思考、调用与观察），供 Web 界面展示推理过程。

幻觉防御（IT-014）：小模型可能不发起 tool_calls，而是在文本里"模拟"工具执行
（输出 Thought:/Action:/Observation: 叙述并编造数据）。本循环对此做检测：
- 若最终回答带模拟特征且本次运行从未真正调用工具 → 注入纠正消息重试一次
- AgentResult.used_tools 标记本次是否经过真实工具查询，供 Web 展示可信度提示
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from src.agent.conversation import Conversation
from src.agent.prompts import (
    FINAL_ANSWER_NUDGE,
    SIMULATED_ANSWER_NUDGE,
    SYSTEM_PROMPT,
)
from src.agent.protocols import SupportsChat
from src.mcp_tools.registry import ToolRegistry

# 文本模拟工具执行的特征（模型编造时几乎必然出现这些协议词+叙述结构）
_SIMULATION_RE = re.compile(r"(Thought\s*:|Action\s*:|Observation\s*:|最终答案\s*[:：])")


def looks_simulated(answer: str) -> bool:
    """判断一段回答文本是否具有'模拟工具执行'特征。"""
    return bool(answer) and bool(_SIMULATION_RE.search(answer))


@dataclass
class AgentResult:
    """一次问答的完整结果。"""

    answer: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    used_tools: bool = False          # 本次回答是否经过真实工具查询
    retried_for_simulation: bool = False  # 是否因幻觉模拟触发过纠正重试

    def trace_summary(self) -> str:
        """渲染人类可读的推理过程（Web 展示用）。"""
        lines = []
        for i, t in enumerate(self.trace, 1):
            lines.append(f"[步骤{i}] 思考: {t['thought'] or '（无）'}")
            for tc in t["tool_calls"]:
                lines.append(f"  -> 调用 {tc['name']}({tc['arguments']})")
            if t["observation"] is None:
                lines.append("  <= 给出最终回答")
            else:
                lines.append(f"  <- 观察: {t['observation'][:200]}")
        return "\n".join(lines)


class ReActAgent:
    """基于 function calling 的 ReAct 循环。

    llm 只要求实现 chat(messages, tools, ...) 接口（见 protocols），真实
    LLMClient 与测试替身可互换。
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

    def run(self, question: str, history: Conversation | None = None) -> AgentResult:
        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        if history:
            messages.extend(history.as_list())
        messages.append({"role": "user", "content": question})

        result = self._run_loop(messages)

        # 幻觉防御：回答带模拟特征且从未真正调用工具 → 注入纠正消息重试一次
        if looks_simulated(result.answer) and not result.used_tools:
            if self.verbose:
                logger.warning("检测到模拟式回答（未调用任何工具），注入纠正消息重试")
            messages.append({"role": "assistant", "content": result.answer})
            messages.append({"role": "user", "content": SIMULATED_ANSWER_NUDGE})
            retried = self._run_loop(messages, start_step=result.steps)
            retried.retried_for_simulation = True
            retried.used_tools = retried.used_tools or result.used_tools
            return retried

        return result

    def _run_loop(self, messages: list[dict], start_step: int = 0) -> AgentResult:
        """ReAct 主循环（可被幻觉重试复用，start_step 承接上一轮步数）。"""
        trace: list[dict[str, Any]] = []
        used_tools = False

        for step in range(start_step + 1, self.max_steps + 1):
            msg = self.llm.chat(messages, tools=self.registry.schemas())
            thought = msg.get("content")
            tool_calls = msg.get("tool_calls")

            if not tool_calls:
                # Final Answer
                trace.append({"thought": thought, "tool_calls": [], "observation": None})
                return AgentResult(
                    answer=thought or "",
                    trace=trace,
                    steps=step,
                    used_tools=used_tools,
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": thought,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": tc["function"],
                        }
                        for tc in tool_calls
                    ],
                }
            )

            observations = []
            step_record = {"thought": thought, "tool_calls": [], "observation": ""}
            for tc in tool_calls:
                name = tc["function"]["name"]
                args = tc["function"].get("arguments") or "{}"
                if self.verbose:
                    logger.info("[步骤{}] 调用 {}({})", step, name, args)
                observation = self.registry.call(name, args)
                step_record["tool_calls"].append({"name": name, "arguments": args})
                observations.append(f"工具 {name} 返回: {observation}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": observation,
                    }
                )
            used_tools = True
            step_record["observation"] = "\n".join(observations)
            trace.append(step_record)

        # 达到最大步数：强制收尾
        logger.warning("达到最大步数 {}，强制要求模型给出最终回答", self.max_steps)
        messages.append({"role": "user", "content": FINAL_ANSWER_NUDGE})
        msg = self.llm.chat(messages, tools=None)
        return AgentResult(
            answer=msg.get("content") or "",
            trace=trace,
            steps=self.max_steps,
            used_tools=used_tools,
        )
