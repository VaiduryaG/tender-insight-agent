"""图存储抽象与 networkx 实现。

GraphStore 定义上层依赖的全部接口；NetworkXStore 是 V1.0 的纯内存实现，
JSON 文件持久化（无需数据库服务）。V2.0 的 Neo4jStore 实现同一接口即可切换。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterator

import networkx as nx

from src.knowledge_graph.schema import NodeType, RelationType, node_id

# 节点属性中记录类型与业务键，持久化/反序列化后仍可还原复合键
_KEY_ATTR = "_key"
_TYPE_ATTR = "_type"


class GraphStore(ABC):
    """图存储抽象接口。所有方法以 (node_type, key) 定位节点。"""

    @abstractmethod
    def add_node(self, node_type: NodeType, key: str, props: dict[str, Any] | None = None) -> str:
        """创建/更新节点，返回全局节点 id。重复添加时合并属性。"""

    @abstractmethod
    def add_edge(
        self,
        src: tuple[NodeType, str],
        dst: tuple[NodeType, str],
        rel_type: RelationType,
        props: dict[str, Any] | None = None,
    ) -> None:
        """创建/更新有向边。同类型同方向的边只保留一条，重复添加合并属性。"""

    @abstractmethod
    def get_node(self, node_type: NodeType, key: str) -> dict[str, Any] | None:
        """读取节点属性，不存在返回 None。"""

    @abstractmethod
    def nodes(self, node_type: NodeType | None = None) -> list[tuple[str, dict[str, Any]]]:
        """列出节点 (key, props)，可按类型过滤。"""

    @abstractmethod
    def edges(self, rel_type: RelationType) -> list[tuple[str, str, dict[str, Any]]]:
        """列出某类型的关系 (src_key, dst_key, props)。"""

    @abstractmethod
    def neighbors(
        self,
        node_type: NodeType,
        key: str,
        rel_type: RelationType | None = None,
        direction: str = "out",
    ) -> Iterator[tuple[str, NodeType, dict[str, Any]]]:
        """迭代邻居，产出 (key, node_type, edge_props)。direction: out/in/both。"""

    @abstractmethod
    def subgraph(
        self,
        node_type: NodeType,
        key: str,
        depth: int = 2,
    ) -> dict[str, Any]:
        """以某节点为中心取 N 跳子图，返回可直接渲染的结构。"""

    @abstractmethod
    def stats(self) -> dict[str, int]:
        """图规模统计：各类型节点数、各类型关系数。"""

    @abstractmethod
    def save(self, path: str | Path) -> None: ...

    @abstractmethod
    def load(self, path: str | Path) -> None: ...


class NetworkXStore(GraphStore):
    """基于 networkx.MultiDiGraph 的实现：允许多种关系类型并存。"""

    def __init__(self) -> None:
        self._g = nx.MultiDiGraph()

    # ---- 节点 ------------------------------------------------------------

    def add_node(self, node_type: NodeType, key: str, props: dict[str, Any] | None = None) -> str:
        nid = node_id(node_type, key)
        base = {_TYPE_ATTR: NodeType(node_type).value, _KEY_ATTR: key}
        if self._g.has_node(nid):
            self._g.nodes[nid].update(props or {})
        else:
            self._g.add_node(nid, **base, **(props or {}))
        return nid

    def get_node(self, node_type: NodeType, key: str) -> dict[str, Any] | None:
        nid = node_id(node_type, key)
        if not self._g.has_node(nid):
            return None
        return {k: v for k, v in self._g.nodes[nid].items() if not k.startswith("_")}

    def nodes(self, node_type: NodeType | None = None) -> list[tuple[str, dict[str, Any]]]:
        prefix = f"{node_type.value}:" if node_type else ""
        return [
            (data[_KEY_ATTR], {k: v for k, v in data.items() if not k.startswith("_")})
            for nid, data in self._g.nodes(data=True)
            if nid.startswith(prefix)
        ]

    # ---- 边 --------------------------------------------------------------

    def add_edge(
        self,
        src: tuple[NodeType, str],
        dst: tuple[NodeType, str],
        rel_type: RelationType,
        props: dict[str, Any] | None = None,
    ) -> None:
        u = node_id(*src)
        v = node_id(*dst)
        if not (self._g.has_node(u) and self._g.has_node(v)):
            raise KeyError(f"add_edge 前需先创建节点: {u} / {v}")
        # 同 key 的关系只保留一条：合并属性（如多次中标合并金额）
        if self._g.has_edge(u, v, key=rel_type.value):
            self._g[u][v][rel_type.value].update(props or {})
        else:
            self._g.add_edge(u, v, key=rel_type.value, **(props or {}))

    def edges(self, rel_type: RelationType) -> list[tuple[str, str, dict[str, Any]]]:
        out = []
        for u, v, key, data in self._g.edges(keys=True, data=True):
            if key == rel_type.value:
                out.append((self._g.nodes[u][_KEY_ATTR], self._g.nodes[v][_KEY_ATTR], data))
        return out

    # ---- 遍历 ------------------------------------------------------------

    def neighbors(
        self,
        node_type: NodeType,
        key: str,
        rel_type: RelationType | None = None,
        direction: str = "out",
    ) -> Iterator[tuple[str, NodeType, dict[str, Any]]]:
        nid = node_id(node_type, key)
        if not self._g.has_node(nid):
            return

        if direction == "out":
            view = self._g.out_edges(nid, keys=True, data=True)
        elif direction == "in":
            view = self._g.in_edges(nid, keys=True, data=True)
        elif direction == "both":
            view = list(self._g.out_edges(nid, keys=True, data=True)) + list(
                self._g.in_edges(nid, keys=True, data=True)
            )
        else:
            raise ValueError(f"direction 仅支持 out/in/both，收到: {direction}")

        seen: set[str] = set()
        for u, v, ekey, data in view:
            if rel_type and ekey != rel_type.value:
                continue
            other = v if u == nid else u
            if other in seen:
                continue
            seen.add(other)
            other_data = self._g.nodes[other]
            yield other_data[_KEY_ATTR], NodeType(other_data[_TYPE_ATTR]), data

    def subgraph(self, node_type: NodeType, key: str, depth: int = 2) -> dict[str, Any]:
        """BFS 取 N 跳 ego 网络，输出节点/边列表供 Pyvis 渲染。"""
        nid = node_id(node_type, key)
        if not self._g.has_node(nid):
            return {"nodes": [], "edges": []}

        dist = {nid: 0}
        queue = [nid]
        while queue:
            cur = queue.pop(0)
            if dist[cur] >= depth:
                continue
            for nbr in list(self._g.successors(cur)) + list(self._g.predecessors(cur)):
                if nbr not in dist:
                    dist[nbr] = dist[cur] + 1
                    queue.append(nbr)

        nodes = [
            {
                "id": other,
                "key": self._g.nodes[other][_KEY_ATTR],
                "type": self._g.nodes[other][_TYPE_ATTR],
                **{k: v for k, v in self._g.nodes[other].items() if not k.startswith("_")},
            }
            for other in dist
        ]
        edges = [
            {
                "source": self._g.nodes[u][_KEY_ATTR],
                "target": self._g.nodes[v][_KEY_ATTR],
                "source_id": u,
                "target_id": v,
                "type": ekey,
                **data,
            }
            for u, v, ekey, data in self._g.edges(keys=True, data=True)
            if u in dist and v in dist
        ]
        return {"nodes": nodes, "edges": edges}

    # ---- 统计与持久化 ------------------------------------------------------

    def stats(self) -> dict[str, int]:
        node_counts: dict[str, int] = {}
        for _, data in self._g.nodes(data=True):
            t = data[_TYPE_ATTR]
            node_counts[t] = node_counts.get(t, 0) + 1
        edge_counts: dict[str, int] = {}
        for _, _, key, _ in self._g.edges(keys=True, data=True):
            edge_counts[key] = edge_counts.get(key, 0) + 1
        return {"nodes": dict(sorted(node_counts.items())), "edges": dict(sorted(edge_counts.items()))}

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = nx.node_link_data(self._g, edges="links")
        path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")

    def load(self, path: str | Path) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self._g = nx.node_link_graph(data, edges="links")
