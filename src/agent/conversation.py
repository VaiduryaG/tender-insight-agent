"""多轮对话历史管理。"""

from __future__ import annotations


class Conversation:
    """消息列表管理：记录历史、按轮次裁剪，防止长对话撑爆上下文。"""

    def __init__(self, max_messages: int = 40):
        self.max_messages = max_messages
        self.messages: list[dict] = []

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        self._trim()

    def add_exchange(self, question: str, answer: str) -> None:
        """记录一轮完整问答（供下轮作为上下文）。"""
        self.add("user", question)
        self.add("assistant", answer)

    def as_list(self) -> list[dict]:
        return list(self.messages)

    def _trim(self) -> None:
        # 裁剪保留完整的 user/assistant 对（偶数索引处切分）
        if len(self.messages) > self.max_messages:
            excess = len(self.messages) - self.max_messages
            self.messages = self.messages[excess + excess % 2 :]

    def clear(self) -> None:
        self.messages.clear()
