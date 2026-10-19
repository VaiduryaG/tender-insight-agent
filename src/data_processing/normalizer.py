"""字段格式标准化：金额、日期、文本、技术标签。

输入是"结构性干净"的数据（已经过 cleaner），输出是类型统一、可直接
入库和构建图谱的标准化数据。
"""

from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

# 常见中文数量级单位 → 换算系数
_UNIT_FACTORS = {
    "万亿": 1_000_000_000_000,
    "亿": 100_000_000,
    "千万": 10_000_000,
    "万": 10_000,
}

# 招投标公告中金额的常见写法，如 "208.8万元"、"500万(元)"、"1,163,909.08元"
_AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(万亿|亿|千万|万)?\s*元?")


def normalize_amount(value: object) -> float | None:
    """将各种金额写法归一化为「元」的浮点数。

    支持: 2088000 / "2088000" / "208.8万" / "208.8万元" / "1,163,909.08元" / "0.5亿"
    无法解析返回 None。
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    text = str(value).strip().replace("，", ",")
    if not text:
        return None

    m = _AMOUNT_RE.search(text)
    if not m:
        return None

    try:
        number = float(m.group(1).replace(",", ""))
    except ValueError:
        return None

    unit = m.group(2)
    if unit:
        number *= _UNIT_FACTORS[unit]
    return round(number, 2)


def normalize_date(value: object) -> str | None:
    """将日期归一化为 ISO 格式 YYYY-MM-DD，无法解析返回 None。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    text = str(value).strip()

    # 2026年9月8日 / 2026-09-08 / 2026.9.8 / 2026/09/08
    m = re.search(r"(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})", text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return None

    # 交给 pandas 兜底（ISO 变体等）
    parsed = pd.to_datetime(text, errors="coerce")
    return None if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def normalize_text(value: object) -> str | None:
    """文本归一化：去首尾空白、压缩连续空白、全角转半角空格。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().replace("　", " ")
    return re.sub(r"\s+", " ", text) or None


def normalize_tech_tags(value: object) -> list[str]:
    """技术标签解析为去重列表。

    输入形如 "物联网;智慧城市;人工智能"（分号/顿号/逗号分隔），输出为
    去除空白后的去重标签列表，保持原顺序。
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    parts = re.split(r"[;；,，、/|]+", str(value))
    seen: list[str] = []
    for part in parts:
        tag = part.strip()
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """对整个 DataFrame 做字段标准化，追加 *_normalized 列，保留原始列。"""
    df = df.copy()

    df["amount_normalized"] = df["amount"].apply(normalize_amount)
    df["announcement_date_normalized"] = df["announcement_date"].apply(normalize_date)

    for col in ["title", "company_name", "purchaser", "industry", "summary"]:
        df[f"{col}_normalized"] = df[col].apply(normalize_text)

    df["tech_tags_list"] = df["tech_tags"].apply(normalize_tech_tags)

    # 日期解析失败的行剔除（无法参与时间分析）
    df = df[df["announcement_date_normalized"].notna()].reset_index(drop=True)

    return df
