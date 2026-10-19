"""结构性数据清洗：去重、必填字段校验、异常值过滤。

设计原则：清洗只做"结构性"判断（行级去重、字段是否缺失、数值是否合理），
不改动字段内容本身——格式标准化交给 normalizer。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# 必填字段：缺失即整行剔除（无法进入知识图谱）
REQUIRED_FIELDS = ["title", "company_name", "amount", "announcement_date", "province", "city"]

# 可选字段：缺失仅填充默认值，不剔除
OPTIONAL_DEFAULTS = {
    "purchaser": "未知",
    "project_id": "",
    "industry": "未分类",
    "tech_tags": "",
    "summary": "",
    "source_url": "",
}

# 金额合理区间（元）：低于 1 万视为噪声，高于 100 亿视为解析错误
AMOUNT_MIN = 10_000
AMOUNT_MAX = 10_000_000_000


@dataclass
class CleaningReport:
    """清洗过程统计，用于生成质量报告。"""

    total_input: int = 0
    dropped_missing: list[str] = field(default_factory=list)
    dropped_duplicate: int = 0
    dropped_amount_outlier: list[str] = field(default_factory=list)
    filled_optional: int = 0
    total_output: int = 0

    def summary(self) -> str:
        lines = [
            f"输入: {self.total_input} 条",
            f"剔除-必填缺失: {len(self.dropped_missing)} 条"
            + (f" ({', '.join(self.dropped_missing)})" if self.dropped_missing else ""),
            f"剔除-整行重复: {self.dropped_duplicate} 条",
            f"剔除-金额越界: {len(self.dropped_amount_outlier)} 条",
            f"填充-可选字段默认值: {self.filled_optional} 处",
            f"输出: {self.total_output} 条",
        ]
        return "\n".join(lines)


class DataCleaner:
    """对原始招投标 DataFrame 做结构性清洗。"""

    def clean(self, df: pd.DataFrame) -> tuple[pd.DataFrame, CleaningReport]:
        report = CleaningReport(total_input=len(df))
        df = df.copy()

        df = self._strip_whitespace(df)
        df, report = self._drop_missing_required(df, report)
        df, report = self._drop_duplicates(df, report)
        df, report = self._drop_amount_outliers(df, report)
        df, report = self._fill_optional(df, report)

        report.total_output = len(df)
        return df.reset_index(drop=True), report

    # ---- 内部步骤 -------------------------------------------------------

    @staticmethod
    def _strip_whitespace(df: pd.DataFrame) -> pd.DataFrame:
        """去除文本字段首尾空白（含全角空格）。"""
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].apply(
                    lambda v: v.strip().replace("　", " ") if isinstance(v, str) else v
                )
        return df

    @staticmethod
    def _drop_missing_required(
        df: pd.DataFrame, report: CleaningReport
    ) -> tuple[pd.DataFrame, CleaningReport]:
        mask_missing = df[REQUIRED_FIELDS].isna().any(axis=1) | (
            df[REQUIRED_FIELDS].astype(str).apply(lambda s: s.str.strip() == "").any(axis=1)
        )
        for title in df.loc[mask_missing, "title"]:
            report.dropped_missing.append(str(title)[:40])
        return df[~mask_missing], report

    @staticmethod
    def _drop_duplicates(
        df: pd.DataFrame, report: CleaningReport
    ) -> tuple[pd.DataFrame, CleaningReport]:
        """整行重复或 (project_id 非空且相同) 视为重复，保留首条。"""
        before = len(df)
        df = df.drop_duplicates(keep="first")
        if "project_id" in df.columns:
            has_id = df["project_id"].astype(str).str.strip() != ""
            df = pd.concat([df[~has_id], df[has_id].drop_duplicates(subset=["project_id"])])
        report.dropped_duplicate = before - len(df)
        return df, report

    @staticmethod
    def _drop_amount_outliers(
        df: pd.DataFrame, report: CleaningReport
    ) -> tuple[pd.DataFrame, CleaningReport]:
        """金额转数值后过滤越界值（无法解析的留待 normalizer 处理，此处不删）。"""
        amounts = pd.to_numeric(df["amount"], errors="coerce")
        outlier_mask = amounts.notna() & ((amounts < AMOUNT_MIN) | (amounts > AMOUNT_MAX))
        for title in df.loc[outlier_mask, "title"]:
            report.dropped_amount_outlier.append(f"{str(title)[:30]}({amounts[outlier_mask].iloc[0]:,.0f}元)")
        return df[~outlier_mask], report

    @staticmethod
    def _fill_optional(
        df: pd.DataFrame, report: CleaningReport
    ) -> tuple[pd.DataFrame, CleaningReport]:
        for col, default in OPTIONAL_DEFAULTS.items():
            if col not in df.columns:
                df[col] = default
            empty = df[col].isna() | (df[col].astype(str).str.strip() == "")
            report.filled_optional += int(empty.sum())
            df.loc[empty, col] = default
        return df, report
