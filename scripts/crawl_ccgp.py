"""V2.0 爬虫入口：抓取中国政府采购网中标公告 → 原始 JSONL。

用法:
    ./run.sh scripts/crawl_ccgp.py --pages 2        # 爬列表前2页的未爬条目
    ./run.sh scripts/crawl_ccgp.py --pages 2 --limit 20   # 限制本次详情页请求数
    ./run.sh scripts/crawl_ccgp.py --list-only      # 只刷新列表不抓详情（低消耗摸底）

安全设计（方案第二章）：
- RateLimiter 默认 5-8s 间隔 + 每10请求长休息
- 单日请求上限保护（--max-requests，默认200）
- 遇 WAF 403 或熔断立即退出
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger

from src.crawler.base import CrawlingBlocked, PoliteSession, RateLimiter
from src.crawler.ccgp import CcgpCrawler
from src.crawler.storage import RawAnnouncement, RawStore


def main() -> None:
    parser = argparse.ArgumentParser(description="中国政府采购网中标公告爬虫（保守低速版）")
    parser.add_argument("--pages", type=int, default=2, help="爬取列表页数（每页约20条）")
    parser.add_argument("--limit", type=int, default=0, help="本次最多抓取的详情页数（0=不限制）")
    parser.add_argument("--max-requests", type=int, default=200, help="单次运行请求上限（方案红线）")
    parser.add_argument("--list-only", action="store_true", help="只解析列表页不抓详情")
    args = parser.parse_args()

    store = RawStore()
    session = PoliteSession(
        rate_limiter=RateLimiter(min_delay=5.0, max_delay=8.0, rest_every=10),
    )
    crawler = CcgpCrawler(session)

    budget = {"n": 0}  # 已发请求数

    def budget_ok() -> bool:
        return budget["n"] < args.max_requests

    # 1) 收集列表条目（增量过滤）
    all_items = []
    for page in range(args.pages):
        if not budget_ok():
            logger.warning("达到单次请求上限 {}，停止", args.max_requests)
            break
        budget["n"] += 1
        try:
            items = crawler.crawl_list_page(page)
        except CrawlingBlocked as e:
            logger.error("{}", e)
            sys.exit(2)
        new_items = store.unseen(items)
        logger.info("列表第{}页: {} 条，其中新 {} 条", page, len(items), len(new_items))
        all_items.extend(new_items)

    if args.list_only:
        for it in all_items:
            print(f"  {it.publish_time} | {it.region:4} | {it.title[:40]}")
        print(f"共 {len(all_items)} 条新公告待抓取")
        return

    # 2) 逐条抓取详情
    ok = fail = 0
    for it in all_items:
        if not budget_ok():
            logger.warning("达到单次请求上限 {}，收工（下次运行自动续爬）", args.max_requests)
            break
        if args.limit and ok >= args.limit:
            logger.info("达到 --limit {}，收工", args.limit)
            break

        budget["n"] += 1
        try:
            body = crawler.crawl_detail(it.url)
        except CrawlingBlocked as e:
            logger.error("{}", e)
            sys.exit(2)
        except Exception as e:
            fail += 1
            logger.error("详情抓取失败 {}: {}", it.url, str(e)[:80])
            continue

        store.save(
            RawAnnouncement(
                url=it.url,
                title=it.title,
                publish_time=it.publish_time,
                region=it.region,
                purchaser_from_list=it.purchaser,
                body_text=body,
                crawled_at=datetime.now().isoformat(timespec="seconds"),
            )
        )
        ok += 1
        logger.info("[{}/{}] 已入库 {} 条: {}", ok, len(all_items), store.count(), it.title[:30])

    logger.info("本次完成: 新入库 {} 条, 失败 {} 条, 总库存 {}", ok, fail, store.count())
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
