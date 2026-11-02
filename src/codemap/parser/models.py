"""Data models for the parsed codebase graph.

Kept dependency-free (pure dataclasses) so the parser can be unit-tested
and reused without pulling in networkx/fastapi.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

NodeType = Literal["module", "class", "function", "method"]
EdgeType = Literal["contains", "imports", "calls"]


@dataclass
class Node:
    id: str  # qualified name, e.g. "pkg.mod.Class.method"
    type: NodeType
    name: str  # short name, e.g. "method"
    qualified_name: str
    file: str  # repo-relative path
    lineno: int
    end_lineno: int
    docstring: str | None = None
    is_entrypoint: bool = False  # module with `if __name__ == "__main__"` or def main()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Edge:
    source: str  # node id
    target: str  # node id
    type: EdgeType
    evidence: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParseResult:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    # Edges whose target could not be resolved to a known node id. Surfaced
    # honestly rather than silently dropped -- dynamic dispatch means we
    # cannot resolve every call statically, and pretending otherwise would
    # be dishonest.
    unresolved_calls: int = 0
    files_parsed: int = 0
    files_failed: int = 0
    diagnostics: list[dict] = field(default_factory=list)
    # Source snapshots stay server-side so citations match the analyzed graph.
    sources: dict[str, str] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "stats": {
                "node_count": len(self.nodes),
                "edge_count": len(self.edges),
                "unresolved_calls": self.unresolved_calls,
                "files_parsed": self.files_parsed,
                "files_failed": self.files_failed,
            },
        }
