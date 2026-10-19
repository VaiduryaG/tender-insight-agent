"""智谱AI LLM 客户端封装（OpenAI 兼容接口）。

统一返回纯 dict 消息结构，屏蔽 SDK 类型差异，便于测试替身替换：
    {"content": str | None,
     "tool_calls": [{"id": str, "function": {"name": str, "arguments": str}}] | None}

重试策略（IT-017 教训）：
- 可重试错误：网络异常、超时、限流(429)、服务端错误(5xx)——指数退避重试
- 不可重试错误：客户端错误(4xx)——立即抛出，避免吞掉真实异常
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

ENV_PATH = Path(".env")
DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4-flash"

# OpenAI SDK 异常分类（IT-017）
_RETRYABLE_ERRORS = ()  # 延迟导入后填充
_NON_RETRYABLE_ERRORS = ()  # 延迟导入后填充


def _init_error_classes():
    """延迟导入 openai 异常类，避免模块级依赖。"""
    global _RETRYABLE_ERRORS, _NON_RETRYABLE_ERRORS
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        InternalServerError,
        NotFoundError,
        PermissionDeniedError,
        RateLimitError,
    )

    _RETRYABLE_ERRORS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
    _NON_RETRYABLE_ERRORS = (BadRequestError, AuthenticationError, PermissionDeniedError, NotFoundError)


class LLMClient:
    """对话补全客户端。tools 传入 OpenAI function calling 格式的 schema 列表。"""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        if ENV_PATH.exists():
            load_dotenv(ENV_PATH)
        self.model = model
        self._api_key = api_key or os.getenv("ZHIPU_API_KEY")
        self._base_url = base_url or os.getenv("ZHIPU_BASE_URL") or DEFAULT_BASE_URL
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not (self._api_key or "").strip():
                raise RuntimeError("ZHIPU_API_KEY 未配置（.env 或环境变量）")
            from openai import OpenAI  # 延迟导入

            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> dict:
        """发送对话，返回统一 dict 消息。

        重试策略（IT-017）：
        - 可重试错误（网络/超时/限流/5xx）：指数退避重试最多3次
        - 不可重试错误（4xx客户端错误）：立即抛出原始异常，不吞掉真实原因
        """
        if not _RETRYABLE_ERRORS:
            _init_error_classes()

        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                kwargs: dict = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if tools:
                    kwargs["tools"] = tools
                resp = self._get_client().chat.completions.create(**kwargs)
                msg = resp.choices[0].message
                content = self._clean_content(msg.content)
                tool_calls = None
                if getattr(msg, "tool_calls", None):
                    tool_calls = [
                        {
                            "id": tc.id,
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in msg.tool_calls
                    ]
                return {"content": content, "tool_calls": tool_calls}

            except _NON_RETRYABLE_ERRORS as e:
                # 4xx 客户端错误：立即抛出，不重试（IT-017 教训）
                # 这些错误表明请求本身有问题，重试只会浪费时间并隐藏真实原因
                logger.error("LLM 客户端错误（不可重试）: {} - {}", type(e).__name__, str(e)[:120])
                raise

            except _RETRYABLE_ERRORS as e:
                # 网络异常/超时/限流/5xx：可重试
                last_err = e
                wait = 2**attempt
                logger.warning("LLM 请求失败（第{}次，可重试），{}s 后重试: {}", attempt, wait, str(e)[:80])
                time.sleep(wait)

            except Exception as e:
                # 未知异常：记录并抛出，不重试
                logger.error("LLM 未知异常（不重试）: {} - {}", type(e).__name__, str(e)[:120])
                raise

        raise RuntimeError(f"LLM 请求连续 3 次失败（可重试错误）") from last_err

    @staticmethod
    def _clean_content(content: str | None) -> str | None:
        """过滤智谱 API 偶发拼接在 content 尾部的流式 chunk 原始 JSON。

        异常形态：正常思考文本后紧跟一段 '{"index":0,"finish_reason":...,
        "delta":...}' 的 chunk 序列化文本。检测到流式元数据特征即从
        '{"index"' 标记处截断（正常招投标回答不会包含该模式）。
        """
        if not content:
            return content
        marker = '{"index"'
        if '"finish_reason"' in content or '"delta"' in content:
            content = content.split(marker)[0].rstrip() or None
        return content
