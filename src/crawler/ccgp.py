"""中国政府采购网（ccgp.gov.cn）爬虫。

基于 D1 技术验证结论（docs/v2_crawler_plan.md）：
- 列表页 index.htm / index_1.htm / ...，静态 HTML
- 详情链接为相对路径 ./YYYYMM/tXXXXXX.htm，<li> 自带发布时间/地域/采购人
- 分页由 JS Pager 渲染，但分页 URL 规律固定，直接拼 URL 即可
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from src.crawler.base import PoliteSession

BASE_URL = "https://www.ccgp.gov.cn"
LIST_URL = f"{BASE_URL}/cggg/dfgg/zbgg/index.htm"

# 详情链接相对路径：./YYYYMM/tYYYYMMDD_数字.htm
_DETAIL_RE = re.compile(r"^\./\d{6}/t\d{8}_\d+\.htm$")


@dataclass
class ListItem:
    """列表页条目（<li> 自带的元数据，D1 验证的意外收获）。"""

    url: str            # 详情页绝对 URL
    title: str
    publish_time: str   # "2026-09-16 17:22"
    region: str         # "浙江"
    purchaser: str      # 列表页自带的采购人（可能为"详情见公告正文"）


class CcgpCrawler:
    """中标公告列表+详情爬取。所有请求经 PoliteSession 限速。"""

    def __init__(self, session: PoliteSession):
        self.session = session

    # ---- 列表页 -----------------------------------------------------------

    def parse_list(self, html: str) -> list[ListItem]:
        """解析列表页 HTML → 条目列表（纯解析，无请求，可单测）。"""
        soup = BeautifulSoup(html, "html.parser")
        items: list[ListItem] = []
        for li in soup.select("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            # D1 验证：详情链接形如 ./202609/t20260916_27339525.htm（相对路径，
            # 不含 /zbgg/ 字样——曾因照搬完整路径过滤条件而解析出0条）
            if not _DETAIL_RE.match(href):
                continue
            ems = li.find_all("em")
            # D1 验证的 <li> 结构：标题链接 + 发布时间 + 地域 + 采购人 四个 <em>
            publish_time = ems[0].get_text(strip=True) if len(ems) > 0 else ""
            region = ems[1].get_text(strip=True) if len(ems) > 1 else ""
            purchaser = ems[2].get_text(strip=True) if len(ems) > 2 else ""
            items.append(
                ListItem(
                    url=self._absolute_url(href),
                    title=a.get("title") or a.get_text(strip=True),
                    publish_time=publish_time,
                    region=region,
                    purchaser=purchaser,
                )
            )
        return items

    def crawl_list_page(self, page: int = 0) -> list[ListItem]:
        """抓取并解析第 page 页（0 = index.htm，1 = index_1.htm）。"""
        url = LIST_URL if page == 0 else f"{BASE_URL}/cggg/dfgg/zbgg/index_{page}.htm"
        html = self.session.get(url, referer=LIST_URL)
        items = self.parse_list(html)
        if not items:
            raise RuntimeError(f"列表页解析出 0 条（页面结构可能变更）: {url}")
        return items

    # ---- 详情页 -----------------------------------------------------------

    def crawl_detail(self, url: str) -> str:
        """抓取详情页，返回正文纯文本（vF_detail_main 容器内）。"""
        html = self.session.get(url, referer=LIST_URL)
        return self.parse_detail(html)

    @staticmethod
    def parse_detail(html: str) -> str:
        """详情页 HTML → 正文纯文本（纯解析，无请求，可单测）。"""
        soup = BeautifulSoup(html, "html.parser")
        container = soup.select_one("div.vF_detail_main") or soup.body or soup
        text = container.get_text("\n")
        # 压缩空白（D1 验证：金额为表格布局，含大量 &nbsp; 与空行）
        lines = [ln.strip() for ln in text.splitlines()]
        return "\n".join(ln for ln in lines if ln)

    # ---- 内部 -------------------------------------------------------------

    @staticmethod
    def _absolute_url(href: str) -> str:
        if href.startswith("http"):
            return href
        # 列表页链接形如 ./202609/tXXX.htm → 去掉 "./" 拼 base
        return f"{BASE_URL}/cggg/dfgg/zbgg/{href.lstrip('./')}"
