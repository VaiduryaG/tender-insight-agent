"""工具注册表：Agent 与 MCP Server 共用的工具定义与调用入口。

工具统一以「JSON Schema 描述 + 可调用函数」注册；调用结果序列化为
JSON 字符串并做长度截断，保证注入对话上下文时长度可控。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_RESULT_CHARS = 4000


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]          # JSON Schema
    func: Callable[..., Any]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """装饰器：注册工具函数。"""

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[name] = Tool(name, description, parameters, fn)
            return fn

        return deco

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self.tools.values()]

    def call(self, name: str, arguments: str | dict) -> str:
        """调用工具并返回 JSON 字符串（异常转为错误信息，绝不向上抛）。"""
        tool = self.tools.get(name)
        if tool is None:
            return json.dumps({"error": f"未知工具: {name}", "available": list(self.tools)}, ensure_ascii=False)
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"参数不是合法JSON: {e}"}, ensure_ascii=False)
        try:
            result = tool.func(**args)
        except TypeError as e:
            return json.dumps({"error": f"参数不匹配: {e}"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"工具执行失败: {type(e).__name__}: {e}"}, ensure_ascii=False)
        return _truncate(json.dumps(result, ensure_ascii=False, default=str))

    def names(self) -> list[str]:
        return list(self.tools)


def _truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f'...[截断，原文{len(text)}字符]'
