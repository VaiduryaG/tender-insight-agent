"""知识图谱模块：图谱建模（schema）、存储抽象（store）、构建（builder）、查询（queries）。

设计要点：上层（builder/queries/工具层）只依赖 GraphStore 抽象接口，
不感知底层实现（V1.0 = NetworkXStore，V2.0 补 Neo4jStore 时零改动）。
"""

from src.knowledge_graph.schema import NodeType, RelationType, node_id
from src.knowledge_graph.store import GraphStore, NetworkXStore

__all__ = ["NodeType", "RelationType", "node_id", "GraphStore", "NetworkXStore"]
