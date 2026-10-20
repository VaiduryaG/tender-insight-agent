"""V2.0 爬虫测试（离线：解析逻辑用 fixture，网络层用 stub）。

运行: ./run.sh -m pytest tests/test_crawler.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.crawler.base import (
    MAX_CONSECUTIVE_FAILURES,
    CrawlingBlocked,
    PoliteSession,
    RateLimiter,
)
from src.crawler.ccgp import CcgpCrawler
from src.crawler.storage import RawAnnouncement, RawStore

FIXTURE_LIST = Path("tests/fixtures/ccgp_list_sample.html")


@pytest.fixture()
def crawler() -> CcgpCrawler:
    return CcgpCrawler(PoliteSession(rate_limiter=RateLimiter(min_delay=0, max_delay=0)))


# ---------------------------------------------------------------------------
# 列表页解析（D1 结论的回归用例）
# ---------------------------------------------------------------------------


def test_parse_list_items(crawler):
    items = crawler.parse_list(FIXTURE_LIST.read_text(encoding="utf-8"))
    assert len(items) >= 15  # 列表页每页约20条
    first = items[0]
    assert first.url.startswith("https://www.ccgp.gov.cn/cggg/dfgg/zbgg/2026")
    assert first.url.endswith(".htm")
    assert "中标" in first.title or "成交" in first.title
    assert first.publish_time.startswith("2026-09")
    assert first.region  # D1 发现：列表页自带地域
    assert first.purchaser  # 与采购人


def test_parse_list_skips_index_and_external(crawler):
    items = crawler.parse_list(FIXTURE_LIST.read_text(encoding="utf-8"))
    for it in items:
        assert "index" not in it.url
        assert it.url.startswith("https://www.ccgp.gov.cn/cggg/dfgg/zbgg/")


def test_parse_list_relative_url_resolution(crawler):
    """D1 验证：详情链接为 ./YYYYMM/tXXX.htm 相对路径，须拼为绝对 URL。"""
    items = crawler.parse_list(FIXTURE_LIST.read_text(encoding="utf-8"))
    assert all(not it.url.startswith("./") for it in items)


# ---------------------------------------------------------------------------
# 详情页解析
# ---------------------------------------------------------------------------


def test_parse_detail_extracts_body_text():
    """D1 验证：正文在 div.vF_detail_main，压缩空白。"""
    html = """
    <html><body>
      <div class="header">导航 导航 导航</div>
      <div class="vF_detail_main">
        <p>一、项目编号</p>
        <p> 项目X-001 </p>
        <p>中标（成交）金额（元）&nbsp;&nbsp;&nbsp; 2088000 </p>
      </div>
      <script>var a = 1;</script>
    </body></html>
    """
    text = CcgpCrawler.parse_detail(html)
    assert "项目X-001" in text
    assert "2088000" in text
    assert "导航 导航" not in text  # 容器外的内容不进入正文
    assert "\n\n" not in text and "&nbsp;" not in text


def test_parse_detail_fallback_to_body():
    html = "<html><body><p>无detail容器的正文</p></body></html>"
    assert "无detail容器的正文" in CcgpCrawler.parse_detail(html)


# ---------------------------------------------------------------------------
# RawStore：JSONL 落地 + 增量去重（tmp_path 隔离）
# ---------------------------------------------------------------------------


def _mk_raw(url: str) -> RawAnnouncement:
    return RawAnnouncement(
        url=url, title="标题", publish_time="2026-09-16 10:00",
        region="山东", purchaser_from_list="某单位", body_text="正文",
        crawled_at="2026-09-16T10:00:00",
    )


def test_store_save_and_count(tmp_path):
    store = RawStore(raw_dir=tmp_path)
    store.save(_mk_raw("https://a/1"))
    store.save(_mk_raw("https://a/2"))
    store.save(_mk_raw("https://a/1"))  # 幂等：重复save不落盘
    assert store.count() == 2


def test_store_seen_persistence(tmp_path):
    store1 = RawStore(raw_dir=tmp_path)
    store1.save(_mk_raw("https://a/1"))
    # 新实例（模拟下次运行）仍记得已爬 URL —— 断点续爬
    store2 = RawStore(raw_dir=tmp_path)
    assert store2.is_seen("https://a/1")
    assert store2.unseen([_mk_raw("https://a/1"), _mk_raw("https://a/2")]) == [
        _mk_raw("https://a/2")
    ]


def test_store_roundtrip(tmp_path):
    store = RawStore(raw_dir=tmp_path)
    raw = _mk_raw("https://a/1")
    store.save(raw)
    loaded = store.load_all()
    assert len(loaded) == 1 and loaded[0].url == raw.url and loaded[0].body_text == "正文"


# ---------------------------------------------------------------------------
# 网络层：限速/重试/熔断（stub 掉真实网络）
# ---------------------------------------------------------------------------


class StubResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.apparent_encoding = "utf-8"


def test_polite_session_403_raises_blocked(monkeypatch, tmp_path):
    """方案红线：403 即抛 CrawlingBlocked，当日停止，绝不重试。"""
    session = PoliteSession(rate_limiter=RateLimiter(min_delay=0, max_delay=0))
    calls = {"n": 0}

    def fake_get(*a, **kw):
        calls["n"] += 1
        return StubResponse(status_code=403, text="blocked")

    monkeypatch.setattr(session.session, "get", fake_get)
    with pytest.raises(CrawlingBlocked):
        session.get("https://x/1")
    assert calls["n"] == 1  # 一次都不重试


@pytest.mark.parametrize("status", [429, 503])
def test_polite_session_rate_limit_stops(monkeypatch, status):
    """IT-020：429/503 软性限流与 403 同级——立即停止，绝不快速重试。"""
    session = PoliteSession(rate_limiter=RateLimiter(min_delay=0, max_delay=0))
    calls = {"n": 0}

    def fake_get(*a, **kw):
        calls["n"] += 1
        return StubResponse(status_code=status, text="slow down")

    monkeypatch.setattr(session.session, "get", fake_get)
    with pytest.raises(CrawlingBlocked, match="限流信号"):
        session.get("https://x/1")
    assert calls["n"] == 1  # 重试等于顶风作案，一次都不允许


def test_polite_session_retry_then_success(monkeypatch):
    session = PoliteSession(rate_limiter=RateLimiter(min_delay=0, max_delay=0), max_retries=3)
    seq = [StubResponse(500), StubResponse(502), StubResponse(200, "页面内容")]
    monkeypatch.setattr(session.session, "get", lambda *a, **kw: seq.pop(0))
    assert session.get("https://x/1") == "页面内容"


def test_polite_session_breaker(monkeypatch):
    """连续失败达阈值 → 熔断。"""
    session = PoliteSession(rate_limiter=RateLimiter(min_delay=0, max_delay=0), max_retries=1)
    monkeypatch.setattr(session.session, "get", lambda *a, **kw: StubResponse(500))

    from src.crawler.base import MAX_CONSECUTIVE_FAILURES

    with pytest.raises(CrawlingBlocked):
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            try:
                session.get("https://x/1")
            except RuntimeError:
                pass  # 单URL重试耗尽抛 RuntimeError，继续下一个 URL 直至熔断


def test_rate_limiter_defaults_match_plan():
    """方案红线参数固化：5-8s 间隔、每10请求长休息。"""
    rl = RateLimiter()
    assert rl.min_delay >= 5.0 and rl.max_delay <= 8.0
    assert rl.rest_every == 10
