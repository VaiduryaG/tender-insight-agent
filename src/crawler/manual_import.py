"""离线公告导入：浏览器手动保存的 HTML → RawStore（与爬虫数据同管线）。

背景（2026-10-20）：ccgp WAF 封禁了服务器出口 IP，短时间无法解封。
改为"浏览器正常浏览 + 手动另存 HTML + 离线解析"的半自动采集路线——
零自动请求，不存在风控问题；解析/提取/去重/入库全部复用现有管线。

用法（见 scripts/import_manual.py）：
    1. 浏览器打开公告详情页，Ctrl+S 保存（"仅 HTML"即可）
    2. 存入 data/raw/manual/（文件名随意）
    3. 运行 ./run.sh scripts/import_manual.py
    4. 之后 parse_raw.py 正常读取（RawStore 同源），无需任何改动

解析规则：
- 正文：div.vF_detail_main 容器（与在线爬虫同一选择器，复用 parse_detail）
- 标题：h1 > <title>（剥"_中国政府采购网"等站点后缀）
- URL：canonical/og:url 缺失时按标题哈希合成（manual:<md5>），天然幂等去重
- 发布时间/地域/采购人：留空，由正文提取兜底（extract.py 的 announce_time/
  region/purchaser 字段，parse_raw.py 已有回退逻辑）
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from src.crawler.base import decode_html
from src.crawler.ccgp import CcgpCrawler

# 浏览器另存 HTML 的默认目录
MANUAL_DIR = Path("data/raw/manual")

# <title> 里的站点后缀（剥掉后即公告标题）
_TITLE_SUFFIXES = (
    "_中国政府采购网",
    "-中国政府采购网",
    "－中国政府采购网",
    "—中国政府采购网",
    "中国政府采购网",
    "中国政府采购",
)

# 详情链接模式（用于识别"用户误存了列表页"）：相对 ./YYYYMM/tYYYYMMDD_N.htm
# 或绝对 https://www.ccgp.gov.cn/cggg/dfgg/zbgg/YYYYMM/t....htm
_DETAIL_LINK_RE = re.compile(r"^(?:\./)?\d{6}/t\d{8}_\d+\.htm$")
_ABS_DETAIL_LINK_RE = re.compile(r"/zbgg/\d{6}/t\d{8}_\d+\.htm")


def _is_detail_link(href: str) -> bool:
    return bool(_DETAIL_LINK_RE.match(href)) or bool(_ABS_DETAIL_LINK_RE.search(href))


@dataclass
class ManualParseResult:
    """单个 HTML 文件的离线解析结果。"""

    ok: bool
    reason: str = ""      # 失败原因（ok=False 时）
    title: str = ""
    url: str = ""
    body_text: str = ""


def parse_saved_page(html: str, source_name: str = "") -> ManualParseResult:
    """浏览器另存的详情页 HTML → 标题/合成URL/正文纯文本。

    纯解析无 IO 无网络，可单测。source_name 仅用于错误信息定位文件。
    """
    soup = BeautifulSoup(html, "html.parser")

    # ---- 列表页识别：无正文容器但有一堆详情链接 → 提示误存 ---------------
    main = soup.select_one("div.vF_detail_main")
    if main is None:
        detail_links = [
            a for a in soup.find_all("a", href=True) if _is_detail_link(a["href"])
        ]
        if len(detail_links) >= 5:
            return ManualParseResult(
                ok=False,
                reason=f"{source_name}: 疑似列表页（无正文容器，含 {len(detail_links)} 个详情链接）——请保存公告详情页而非列表页",
            )
        return ManualParseResult(ok=False, reason=f"{source_name}: 未找到正文容器 div.vF_detail_main")

    # ---- 标题：h1 优先，<title> 兜底，剥站点后缀 --------------------------
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        raw_title = soup.title.get_text(strip=True) if soup.title else ""
        for suffix in _TITLE_SUFFIXES:
            if raw_title.endswith(suffix):
                raw_title = raw_title[: -len(suffix)]
                break
        title = raw_title.strip(" _-－—")
    if not title:
        return ManualParseResult(ok=False, reason=f"{source_name}: 未能提取标题（h1 与 <title> 均为空）")

    # ---- URL：canonical > og:url > 标题哈希合成（稳定 → 幂等去重）---------
    url = ""
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href", "").startswith("http"):
        url = canonical["href"]
    elif (og := soup.find("meta", property="og:url")) and og.get("content", "").startswith("http"):
        url = og["content"]
    if not url:
        digest = hashlib.md5(title.encode("utf-8")).hexdigest()[:16]
        url = f"manual:{digest}"

    # ---- 正文：复用在线爬虫同一容器选择器与空白压缩 -----------------------
    body_text = CcgpCrawler.parse_detail(html)
    if not body_text:
        return ManualParseResult(ok=False, reason=f"{source_name}: 正文提取为空")

    return ManualParseResult(ok=True, title=title, url=url, body_text=body_text)


def import_manual_dir(
    manual_dir: Path = MANUAL_DIR, raw_dir: Path | None = None
) -> tuple[int, int, list[str]]:
    """扫描目录内全部 HTML 并入库 RawStore。

    raw_dir 为 None 时用真实存储（data/raw/crawled/）；测试传 tmp_path 隔离。
    返回 (新入库数, 重复跳过数, 失败原因列表)。目录不存在或无文件时全部为 0。
    """
    from loguru import logger

    from src.crawler.storage import RawAnnouncement, RawStore
    from datetime import datetime

    if not manual_dir.exists():
        logger.warning("目录不存在: {}（请先浏览器另存公告 HTML 到该目录）", manual_dir)
        return 0, 0, []

    files = sorted(p for p in manual_dir.iterdir() if p.suffix.lower() in (".html", ".htm"))
    if not files:
        logger.warning("目录为空: {}", manual_dir)
        return 0, 0, []

    store = RawStore(raw_dir) if raw_dir else RawStore()
    imported = skipped = 0
    failures: list[str] = []

    for path in files:
        html = decode_html(path.read_bytes())
        result = parse_saved_page(html, source_name=path.name)

        if not result.ok:
            failures.append(result.reason)
            continue
        if store.is_seen(result.url):
            skipped += 1
            logger.debug("已存在，跳过: {}", result.title[:40])
            continue

        store.save(
            RawAnnouncement(
                url=result.url,
                title=result.title,
                publish_time="",          # 正文提取兜底（announce_time）
                region="",                # 正文提取兜底（region）
                purchaser_from_list="",   # 正文提取兜底（purchaser）
                body_text=result.body_text,
                crawled_at=datetime.now().isoformat(timespec="seconds"),
            )
        )
        imported += 1
        logger.info("入库 [{}] {}", store.count(), result.title[:40])

    return imported, skipped, failures
