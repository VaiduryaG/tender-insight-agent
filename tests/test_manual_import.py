"""离线公告导入测试（manual_import）：保存页解析 / URL合成 / 列表页识别 / 入库幂等。

运行: ./run.sh -m pytest tests/test_manual_import.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.crawler.manual_import import parse_saved_page
from src.crawler.storage import RawStore

# 贴近 ccgp 真实结构的最小详情页模板（浏览器"仅 HTML"另存形态）
_DETAIL_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}_中国政府采购网</title>
</head>
<body>
<div class="vF_detail_main">
<h1>{title}</h1>
<p>公告概要：</p>
<table><tr><td>采购项目名称</td></tr><tr><td>{project}</td></tr></table>
一、项目编号
{pid}
二、项目名称
{project}
三、中标（成交）信息
采购人
{purchaser}
行政区域
浙江省
公告时间
2026年10月20日  10:00
总中标金额
￥100.000000 万元（人民币）
四、评审专家（单一来源采购人员）名单：
五、项目联系方式
</div>
</body>
</html>"""


def _mk_html(title="某某系统采购中标公告"):
    return _DETAIL_HTML.format(
        title=title, project="某某系统", pid="XYZ-2026-001", purchaser="某某单位"
    )


# ---------------------------------------------------------------------------
# parse_saved_page
# ---------------------------------------------------------------------------


def test_parse_detail_page_extracts_title_body():
    r = parse_saved_page(_mk_html(), source_name="a.html")
    assert r.ok
    assert r.title == "某某系统采购中标公告"
    assert "采购项目名称" in r.body_text and "某某系统" in r.body_text
    # 站点后缀已剥（不残留"_中国政府采购网"）
    assert "中国政府采购网" not in r.title


def test_parse_synthesizes_stable_url_from_title():
    """无 canonical/og:url 时按标题哈希合成 URL——同标题同 URL（幂等去重的根基）。"""
    r1 = parse_saved_page(_mk_html("测试公告甲"))
    r2 = parse_saved_page(_mk_html("测试公告甲"))
    r3 = parse_saved_page(_mk_html("测试公告乙"))
    assert r1.url == r2.url
    assert r1.url != r3.url
    assert r1.url.startswith("manual:")


def test_parse_rejects_list_page():
    """用户误存列表页：无正文容器但含 ≥5 个详情链接 → 明确报错提示。"""
    links = "\n".join(
        f'<li><a href="./202610/t20261019_0000{i}.htm">公告{i}</a></li>' for i in range(8)
    )
    html = f"<html><body><ul>{links}</ul></body></html>"
    r = parse_saved_page(html, source_name="index.htm")
    assert not r.ok
    assert "列表页" in r.reason


def test_parse_rejects_empty_page():
    r = parse_saved_page("<html><body><p>hello</p></body></html>", source_name="x.html")
    assert not r.ok


# ---------------------------------------------------------------------------
# import_manual_dir（真实 RawStore 入库，tmp_path 隔离）
# ---------------------------------------------------------------------------


def _seed_store(tmp_path, url):
    store = RawStore(raw_dir=tmp_path / "crawled")
    from src.crawler.storage import RawAnnouncement
    from datetime import datetime

    store.save(
        RawAnnouncement(
            url=url, title="旧", publish_time="", region="",
            purchaser_from_list="", body_text="旧正文",
            crawled_at=datetime.now().isoformat(timespec="seconds"),
        )
    )
    return store


def test_import_manual_dir_roundtrip(tmp_path):
    """正常路径：HTML 文件 → 解析 → RawStore 落盘 → load_all 读回正文。"""
    manual_dir = tmp_path / "manual"
    manual_dir.mkdir()
    (manual_dir / "a.html").write_bytes(_mk_html("公告一号").encode("utf-8"))
    (manual_dir / "b.html").write_bytes(_mk_html("公告二号").encode("gbk"))  # 编码探测路径

    import src.crawler.manual_import as mi

    raw_store_dir = tmp_path / "crawled"
    imported, skipped, failures = mi.import_manual_dir(manual_dir, raw_dir=raw_store_dir)
    assert (imported, skipped, failures) == (2, 0, [])

    from src.crawler.storage import RawStore

    store = RawStore(raw_dir=raw_store_dir)
    records = store.load_all()
    assert len(records) == 2
    titles = {r.title for r in records}
    assert titles == {"公告一号", "公告二号"}
    assert all(r.body_text for r in records)
    assert all(r.url.startswith("manual:") for r in records)


def test_import_manual_dir_dedup_and_idempotent(tmp_path):
    """同文件重复导入不重复入库；与既有 URL 相同的记录跳过。"""
    import src.crawler.manual_import as mi

    manual_dir = tmp_path / "manual"
    manual_dir.mkdir()
    (manual_dir / "a.html").write_bytes(_mk_html("公告三号").encode("utf-8"))

    raw_store_dir = tmp_path / "crawled"
    assert mi.import_manual_dir(manual_dir, raw_dir=raw_store_dir)[0] == 1   # 首次入库
    imported, skipped, _ = mi.import_manual_dir(manual_dir, raw_dir=raw_store_dir)
    assert (imported, skipped) == (0, 1)              # 二次全跳过

    from src.crawler.storage import RawStore

    assert RawStore(raw_dir=raw_store_dir).count() == 1


def test_import_manual_dir_missing_dir(tmp_path):
    import src.crawler.manual_import as mi

    assert mi.import_manual_dir(tmp_path / "不存在") == (0, 0, [])
