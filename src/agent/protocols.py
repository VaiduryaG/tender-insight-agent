"""Agent 层协议：LLM 只需实现 chat 接口即可注入（真实客户端 / 测试替身）。"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SupportsChat(Protocol):
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> dict: ...
