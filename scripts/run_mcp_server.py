"""阶段5入口：启动 MCP Server（stdio 传输）。

启动后进程阻塞监听 stdio，供 MCP 客户端（Claude Desktop 等）连接。
手动验证见 tests/test_stage5.py 的内存会话测试。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger

from src.mcp_tools.server import mcp


def main() -> None:
    logger.remove()  # 关闭 stderr 日志，避免污染 stdio 协议通道
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
