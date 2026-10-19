"""爬虫基础设施：限速 Session、指数退避重试、熔断。

设计原则（对应 docs/v2_crawler_plan.md 第二章）：
- 保守低速：请求间 5-8 秒随机延时，每 N 个请求长休息
- 礼貌身份：完整浏览器请求头（D1 验证：无 UA 会被 WAF 403）
- 快速熔断：连续失败/遇 403 立即停，绝不硬闯
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field

import requests
from loguru import logger

# 完整浏览器请求头（D1 验证：缺失会被 WAF 拦截）
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 熔断阈值
MAX_CONSECUTIVE_FAILURES = 5
BLOCK_STATUS = 403  # WAF 拦截信号，触发即停


class CrawlingBlocked(Exception):
    """WAF 拦截/熔断信号：当日必须停止爬取（方案红线）。"""


@dataclass
class RateLimiter:
    """限速器：随机间隔 + 长休息。"""

    min_delay: float = 5.0          # 方案红线：最小 5 秒
    max_delay: float = 8.0          # 最大 8 秒
    rest_every: int = 10            # 每 N 个请求
    rest_seconds: tuple[float, float] = (60.0, 120.0)  # 长休息区间
    _count: int = field(default=0, repr=False)

    def wait(self) -> None:
        self._count += 1
        if self._count % self.rest_every == 0:
            duration = random.uniform(*self.rest_seconds)
            logger.info("已请求 {} 个，长休息 {:.0f} 秒…", self._count, duration)
            time.sleep(duration)
        else:
            time.sleep(random.uniform(self.min_delay, self.max_delay))


class PoliteSession:
    """带浏览器身份、限速、重试与熔断的请求会话。"""

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        timeout: int = 20,
        max_retries: int = 3,
    ):
        self.session = requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
        self.limiter = rate_limiter or RateLimiter()
        self.timeout = timeout
        self.max_retries = max_retries
        self._consecutive_failures = 0

    def get(self, url: str, referer: str | None = None) -> str:
        """GET 一个页面，返回 HTML 文本。失败重试，WAF拦截/熔断抛 CrawlingBlocked。"""
        headers = {"Referer": referer} if referer else None

        for attempt in range(1, self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(url, timeout=self.timeout, headers=headers)
            except requests.RequestException as e:
                self._consecutive_failures += 1
                logger.warning("网络异常（第{}次）：{} - {}", attempt, type(e).__name__, str(e)[:80])
                self._check_breaker()
                time.sleep(2**attempt)
                continue

            if resp.status_code == 200:
                self._consecutive_failures = 0
                return self._decode(resp)

            if resp.status_code == BLOCK_STATUS:
                # 方案红线：403 即 WAF 拦截，当日停止，绝不重试
                raise CrawlingBlocked(f"WAF 拦截（403）：{url} —— 按方案红线停止当日爬取")

            # 其他状态码（404/500等）：重试
            self._consecutive_failures += 1
            logger.warning("HTTP {}（第{}次）：{}", resp.status_code, attempt, url)
            self._check_breaker()
            time.sleep(2**attempt)

        raise RuntimeError(f"重试 {self.max_retries} 次仍失败: {url}")

    @staticmethod
    def _decode(resp: requests.Response) -> str:
        """解码响应体。

        教训（IT-015）：apparent_encoding（chardet探测）在个别 ccgp 页面误判，
        产出乱码正文（提取全失败）。ccgp 页面在 <meta charset> 声明编码，
        按声明解码；无声明时 utf-8 失败回退 gbk（政府站常见编码）。
        """
        content = resp.content
        head = content[:1024].decode("ascii", errors="ignore")
        declared = None
        m = re.search(r'charset=["\']?([\w-]+)', head, re.I)
        if m:
            declared = m.group(1).lower()

        candidates = []
        if declared:
            candidates.append(declared)
        candidates += ["utf-8", "gbk"]
        for enc in candidates:
            try:
                text = content.decode(enc)
                if "　" * 3 not in text and text.count("�") < 5:  # 乱码启发式检查
                    return text
            except (UnicodeDecodeError, LookupError):
                continue
        return content.decode("utf-8", errors="replace")

    def _check_breaker(self) -> None:
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            raise CrawlingBlocked(
                f"连续 {self._consecutive_failures} 次失败，熔断停止（可能页面结构变更或网络异常）"
            )
