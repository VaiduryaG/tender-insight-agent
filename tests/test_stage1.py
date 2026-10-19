"""阶段1数据处理测试。

运行: ./run.sh -m pytest tests/test_stage1.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from src.data_processing.cleaner import DataCleaner
from src.data_processing.entity_aligner import EntityAligner
from src.data_processing.normalizer import (
    normalize_amount,
    normalize_date,
    normalize_tech_tags,
)


# ---------------------------------------------------------------------------
# 金额标准化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (2088000, 2088000.0),                      # 纯数字
        ("2088000", 2088000.0),                    # 数字字符串
        ("1163909.08", 1163909.08),                # 小数
        ("1,163,909.08元", 1163909.08),            # 千分位+元
        ("208.8万元", 2088000.0),                  # 万元
        ("0.5亿", 50000000.0),                     # 亿
        ("3000万", 30000000.0),                    # 万（无元）
        ("合同金额208.8万元整", 2088000.0),         # 从文本中提取
        ("", None),
        (None, None),
        ("未中标", None),                          # 无法解析
    ],
)
def test_normalize_amount(raw, expected):
    assert normalize_amount(raw) == expected


# ---------------------------------------------------------------------------
# 日期标准化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-09-08", "2026-09-08"),
        ("2026年9月8日", "2026-09-08"),
        ("2026.9.8", "2026-09-08"),
        ("2026/09/08", "2026-09-08"),
        ("公告日期：2026年09月08日发布", "2026-09-08"),  # 从文本中提取
        ("2026-13-01", None),                       # 非法日期
        ("", None),
        (None, None),
    ],
)
def test_normalize_date(raw, expected):
    assert normalize_date(raw) == expected


# ---------------------------------------------------------------------------
# 技术标签
# ---------------------------------------------------------------------------


def test_normalize_tech_tags():
    assert normalize_tech_tags("AI;智慧课堂;云桌面") == ["AI", "智慧课堂", "云桌面"]
    assert normalize_tech_tags("AI、大数据、物联网") == ["AI", "大数据", "物联网"]
    assert normalize_tech_tags("AI;AI;大数据") == ["AI", "大数据"]  # 去重保序
    assert normalize_tech_tags("") == []
    assert normalize_tech_tags(None) == []


# ---------------------------------------------------------------------------
# 清洗
# ---------------------------------------------------------------------------


def _mk_df(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "title", "company_name", "amount", "announcement_date",
            "province", "city", "purchaser", "project_id",
            "industry", "tech_tags", "summary", "source_url",
        ],
    )


def test_clean_drops_missing_required():
    df = _mk_df(
        [
            ("项目A", "公司A", 100000, "2026-09-01", "山东", "济南", "", "", "", "", "", ""),
            ("项目B", "", 100000, "2026-09-01", "山东", "济南", "", "", "", "", "", ""),  # 公司名缺失
        ]
    )
    cleaned, report = DataCleaner().clean(df)
    assert len(cleaned) == 1
    assert report.dropped_missing == ["项目B"]


def test_clean_dups_by_project_id():
    df = _mk_df(
        [
            ("项目A", "公司A", 100000, "2026-09-01", "山东", "济南", "", "ID-1", "", "", "", ""),
            ("项目A", "公司A", 100000, "2026-09-01", "山东", "济南", "", "ID-1", "", "", "", ""),
        ]
    )
    cleaned, report = DataCleaner().clean(df)
    assert len(cleaned) == 1
    assert report.dropped_duplicate == 1


def test_clean_drops_amount_outlier():
    df = _mk_df(
        [
            ("正常项目", "公司A", 5000000, "2026-09-01", "山东", "济南", "", "", "", "", "", ""),
            ("可疑项目", "公司B", 500, "2026-09-01", "山东", "济南", "", "", "", "", "", ""),  # 低于1万
        ]
    )
    cleaned, report = DataCleaner().clean(df)
    assert len(cleaned) == 1
    assert "可疑项目" in report.dropped_amount_outlier[0]


# ---------------------------------------------------------------------------
# 实体对齐（真实数据踩过的坑作为回归用例）
# ---------------------------------------------------------------------------


def _group_of(name: str) -> str:
    return EntityAligner().align([name])[0].group_name


def test_branch_of_guangzhou_telecom():
    """电信两家分公司必须归并到同一集团（回归：正则曾误吞'有限公司'）。"""
    assert _group_of("中国电信股份有限公司温州分公司") == "中国电信"
    assert _group_of("中国电信集团有限公司太原分公司") == "中国电信"


def test_branch_of_unicom():
    """联通分公司集团名不得丢字（回归：曾得到'中国联合网络通'）。"""
    assert _group_of("中国联合网络通信有限公司银川市分公司") == "中国联合网络通信"


def test_iterative_suffix_strip():
    """'股份有限公司'与'集团有限公司'写法归并到同一键。"""
    assert _group_of("中国电信股份有限公司") == "中国电信"
    assert _group_of("中国电信集团有限公司") == "中国电信"


def test_normal_company_group():
    assert _group_of("河北星光未来数字技术有限公司") == "河北星光未来数字技术"


def test_align_is_deterministic_and_shared():
    """同名输入共享同一结果，且多次运行结果一致。"""
    names = ["中国电信股份有限公司温州分公司", "中国电信集团有限公司太原分公司"]
    first = EntityAligner().align(names)
    second = EntityAligner().align(names)
    assert [r.group_name for r in first] == [r.group_name for r in second]
    assert first[0].group_name == first[1].group_name
