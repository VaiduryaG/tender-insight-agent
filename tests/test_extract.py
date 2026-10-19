"""D4 字段提取测试：fixture 来自真实爬取样本（data/raw/crawled）。

运行: ./run.sh -m pytest tests/test_extract.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.crawler.extract import ExtractionResult, FieldExtractor

FIXTURES = Path("tests/fixtures")


@pytest.fixture()
def extractor() -> FieldExtractor:
    return FieldExtractor()


def _body(name: str) -> str:
    return (FIXTURES / f"ccgp_body_{name}.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 真实样本回归
# ---------------------------------------------------------------------------


def test_table_zone_sample(extractor):
    """表格型公告：供应商在中标信息区表头后，金额在'总价：X（元）'。"""
    r = extractor.extract(_body("table"))
    assert r.fields.get("supplier") == "杭州恒益医疗器械有限公司"
    assert r.amount_normalized == 2230000.0
    assert r.sources.get("supplier") == "table_zone"
    assert "purchaser" in r.fields  # 采购单位（概要区键值）
    assert "project_id" in r.fields  # 一、项目编号：后的值


def test_kv_zone_sample(extractor):
    """键值型公告：无表头，供应商在'供应商名称\\nXX公司'结构。"""
    r = extractor.extract(_body("kv"))
    assert r.fields.get("supplier"), f"供应商未提取到: missing={r.missing}"
    assert "公司" in r.fields["supplier"] or "中心" in r.fields["supplier"]
    assert r.amount_normalized and r.amount_normalized > 0
    assert r.fields.get("purchaser")


# ---------------------------------------------------------------------------
# 合成用例：覆盖边界
# ---------------------------------------------------------------------------


def test_inline_colon_style(extractor):
    """冒号行内写法：'供应商名称：XX公司'。"""
    body = "公告正文\n供应商名称：内蒙古某某科技有限公司\n供应商地址：某地\n其他内容"
    r = extractor.extract(body)
    assert r.fields.get("supplier") == "内蒙古某某科技有限公司"
    assert r.sources["supplier"] == "inline"


def test_amount_normalization_variants(extractor):
    for text, expect in [
        ("总中标金额\n￥223.000000 万元（人民币）", 2230000.0),
        ("中标（成交）金额(元)\n总价：3169380（元）", 3169380.0),
        ("成交金额\n1,163,909.08元", 1163909.08),
    ]:
        r = extractor.extract(text)
        assert r.amount_normalized == pytest.approx(expect), f"输入: {text!r}"


def test_missing_fields_marked_not_guessed(extractor):
    """无关文本 → 全部字段进 missing，不猜测。"""
    r = extractor.extract("今天天气不错。\n随便一段与采购无关的文字。")
    assert "supplier" in r.missing
    assert "amount" in r.missing
    assert r.fields.get("supplier") is None


def test_supplier_value_skips_consecutive_headers(extractor):
    """连续表头行（中标供应商名称→中标供应商地址）后才是真值。"""
    body = "三、中标（成交）信息\n1.中标结果：\n序号\n中标（成交）金额(元)\n中标供应商名称\n中标供应商地址\n评审总得分\n1\n总价：500000（元）\n北京某某技术有限公司\n北京市某某区某某路1号\n90.5\n四、主要标的信息"
    r = extractor.extract(body)
    assert r.fields.get("supplier") == "北京某某技术有限公司"
    assert r.amount_normalized == 500000.0


def test_extraction_result_defaults():
    r = ExtractionResult()
    assert r.fields == {} and r.missing == [] and r.amount_normalized is None
