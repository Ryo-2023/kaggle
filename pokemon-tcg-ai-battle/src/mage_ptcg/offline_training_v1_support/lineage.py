"""Lineage graph tracking module.

Supports tracking derived datasets, models, experiments, and screening records.
Detects cycles, missing parents, and topologically orders DAG structures.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from mage_ptcg.offline_training_v1_support.contracts import SupportContractError

VALID_NODE_TYPES = {
    "collection", "dataset", "experiment", "model", "evaluation",
    "screening", "package", "repro bundle", "teacher snapshot", "iteration round"
}

VALID_EDGES = {
    "derived_from", "trained_on", "evaluated_by", "packaged_as",
    "queried_teacher", "continued_from"
}

class LineageGraph:
    """Directed Acyclic Graph (DAG) for tracking lifecycle provenance."""

    def __init__(self):
        self.nodes: dict[str, dict[str, Any]] = {}
        # adjacency list for forward (from_node -> list of (to_node, relation))
        self.adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
        # backward list (to_node -> list of (from_node, relation))
        self.rev_adj: dict[str, list[tuple[str, str]]] = defaultdict(list)

    def add_node(self, node_id: str, node_type: str, metadata: dict[str, Any] | None = None) -> None:
        """Add node to graph."""
        if node_type not in VALID_NODE_TYPES:
            raise SupportContractError(f"Invalid lineage node type: {node_type}")
        self.nodes[node_id] = {
            "type": node_type,
            "metadata": metadata or {},
        }

    def add_edge(self, from_node: str, to_node: str, relation: str) -> None:
        """Add edge to graph."""
        if relation not in VALID_EDGES:
            raise SupportContractError(f"Invalid relation type: {relation}")

        # Check for duplicate edge
        for dest, rel in self.adj[from_node]:
            if dest == to_node and rel == relation:
                return # Duplicate, skip silently

        self.adj[from_node].append((to_node, relation))
        self.rev_adj[to_node].append((from_node, relation))

    def detect_missing_and_orphans(self) -> dict[str, list[str]]:
        """Identify missing nodes (edge targets not in nodes list) and orphans."""
        missing = set()
        referenced = set()

        for u in self.adj:
            referenced.add(u)
            for v, _ in self.adj[u]:
                referenced.add(v)
                if v not in self.nodes:
                    missing.add(v)

        orphans = []
        for n in self.nodes:
            # An orphan has no incoming and no outgoing edges
            if not self.adj[n] and not self.rev_adj[n]:
                orphans.append(n)

        return {
            "missing_nodes": sorted(list(missing)),
            "orphan_nodes": sorted(orphans),
        }

    def find_cycles(self) -> list[list[str]]:
        """Detect and return all cycles in the graph using DFS."""
        visited = {} # id -> state (0 = unvisited, 1 = visiting, 2 = visited)
        cycles = []

        def dfs(u: str, path: list[str]):
            visited[u] = 1
            path.append(u)
            for v, _ in self.adj[u]:
                state = visited.get(v, 0)
                if state == 1:
                    # Cycle detected, trace back
                    idx = path.index(v)
                    cycles.append(path[idx:] + [v])
                elif state == 0:
                    dfs(v, path)
            path.pop()
            visited[u] = 2

        all_nodes = set(self.nodes.keys()) | set(self.adj.keys())
        for n in sorted(list(all_nodes)):
            if visited.get(n, 0) == 0:
                dfs(n, [])

        return cycles

    def get_topological_order(self) -> list[str]:
        """Perform topological sort. Raises error if cycles exist."""
        if self.find_cycles():
            raise SupportContractError("Lineage graph has cycles; cannot compute topological order")

        visited = set()
        order = []

        def dfs(u: str):
            visited.add(u)
            for v, _ in self.adj[u]:
                if v not in visited:
                    dfs(v)
            order.append(u)

        all_nodes = set(self.nodes.keys()) | set(self.adj.keys())
        for n in sorted(list(all_nodes)):
            if n not in visited:
                dfs(n)

        return order[::-1]

    def get_ancestors(self, node_id: str) -> set[str]:
        """Get all node IDs that lead to this node (backward reachability)."""
        visited = set()
        def dfs(u: str):
            for v, _ in self.rev_adj[u]:
                if v not in visited:
                    visited.add(v)
                    dfs(v)
        dfs(node_id)
        return visited

    def get_descendants(self, node_id: str) -> set[str]:
        """Get all node IDs that are reachable from this node (forward reachability)."""
        visited = set()
        def dfs(u: str):
            for v, _ in self.adj[u]:
                if v not in visited:
                    visited.add(v)
                    dfs(v)
        dfs(node_id)
        return visited

    def generate_dot(self) -> str:
        """Generate graph representation in DOT format for graphviz visualization."""
        dot = ["digraph lineage {", "  rankdir=LR;"]

        # Write nodes
        for nid, info in sorted(self.nodes.items()):
            ntype = info["type"]
            dot.append(f'  "{nid}" [label="{nid}\\n({ntype})", shape=box];')

        # Write edges
        for u in sorted(self.adj.keys()):
            for v, rel in sorted(self.adj[u], key=lambda x: x[0]):
                dot.append(f'  "{u}" -> "{v}" [label="{rel}"];')

        dot.append("}")
        return "\n".join(dot)

    def generate_markdown(self) -> str:
        """Generate human-readable Markdown summary of the lineage graph."""
        lines = ["# Lineage Path Summary", ""]
        order = self.get_topological_order()
        lines.append("## Node Processing Sequence (Topological Order)")
        for idx, node in enumerate(order, 1):
            info = self.nodes.get(node, {"type": "unknown"})
            lines.append(f"{idx}. **{node}** (`{info['type']}`)")

        lines.append("")
        lines.append("## Graph Edges Connections")
        for u in sorted(self.adj.keys()):
            for v, rel in sorted(self.adj[u], key=lambda x: x[0]):
                lines.append(f"- `{u}` —[`{rel}`]→ `{v}`")
        return "\n".join(lines)
