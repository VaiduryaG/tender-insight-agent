"""离线公告导入入口：浏览器另存的 HTML → RawStore → parse_raw.py 管线。

用法:
    # 1. 浏览器打开 ccgp 公告详情页，Ctrl+S 另存（"仅 HTML"）到 data/raw/manual/
    # 2. 运行导入
    ./run.sh scripts/import_manual.py

导入后与爬虫数据同库（data/raw/crawled/announcements.jsonl），
直接运行 parse_raw.py → run_stage1.py 即可并入全链路，无需其他改动。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger

from src.crawler.manual_import import MANUAL_DIR, import_manual_dir


def main() -> None:
    imported, skipped, failures = import_manual_dir()

    logger.info("导入完成: 新入库 {} 条, 重复跳过 {} 条, 失败 {} 条", imported, skipped, len(failures))
    for reason in failures:
        logger.warning("  {}", reason)

    if imported:
        logger.info("下一步: ./run.sh scripts/parse_raw.py  （字段提取 → 12列CSV）")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
