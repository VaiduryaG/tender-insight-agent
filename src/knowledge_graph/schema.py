"""图谱建模：节点类型、关系类型与节点键约定。

节点键（key）在各自类型内唯一，全局唯一 id 由 node_id() 拼接为
"{NodeType}:{key}"，与具体存储实现无关。
"""

from __future__ import annotations

import hashlib
from enum import StrEnum


class NodeType(StrEnum):
    COMPANY = "Company"        # 公司（法人实体级，含 group 属性指向集团）
    PROJECT = "Project"        # 招投标项目
    INDUSTRY = "Industry"      # 行业
    TECHNOLOGY = "Technology"  # 技术
    REGION = "Region"          # 地区（省/市）


class RelationType(StrEnum):
    WON = "WON"                        # Company -> Project，属性: amount, date, industry
    BELONGS_TO = "BELONGS_TO"          # Project -> Industry
    REQUIRES = "REQUIRES"              # Project -> Technology
    LOCATED_IN = "LOCATED_IN"          # Project -> Region
    COMPETES_WITH = "COMPETES_WITH"    # Company -> Company，属性: shared_industries


def node_id(node_type: NodeType | str, key: str) -> str:
    """类型内唯一的 key 拼接为全局唯一节点 id。"""
    return f"{NodeType(node_type).value}:{key}"


def project_key(project_id: str, title: str) -> str:
    """项目节点键：优先用采购编号，缺失时用标题哈希兜底（保证幂等）。"""
    pid = (project_id or "").strip()
    if pid:
        return pid
    return "hash-" + hashlib.md5(title.strip().encode()).hexdigest()[:12]
