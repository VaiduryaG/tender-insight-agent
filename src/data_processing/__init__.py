"""数据处理模块：清洗 → 标准化 → 实体对齐。

模块职责：
- cleaner:          结构性清洗（去重、必填校验、异常值过滤）
- normalizer:       字段标准化（金额、日期、文本、技术标签）
- entity_aligner:   公司名称实体对齐（四层策略）与集团归组
"""

from src.data_processing.cleaner import DataCleaner, CleaningReport
from src.data_processing.normalizer import (
    normalize_amount,
    normalize_date,
    normalize_text,
    normalize_tech_tags,
)
from src.data_processing.entity_aligner import EntityAligner

__all__ = [
    "DataCleaner",
    "CleaningReport",
    "EntityAligner",
    "normalize_amount",
    "normalize_date",
    "normalize_text",
    "normalize_tech_tags",
]
