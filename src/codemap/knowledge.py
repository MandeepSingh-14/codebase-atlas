"""Bounded retrieval over one immutable analysis and its source snapshots."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class Analysis:
    id: str
    graph: dict
    sources: dict[str, str]


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Search(Arguments):
    query: str = Field(min_length=1, max_length=500)


class NodeLookup(Arguments):
    node_id: str = Field(min_length=1, max_length=500)


class Neighbors(NodeLookup):
    relationship: Literal["contains", "imports", "calls"] = "calls"
    direction: Literal["incoming", "outgoing"] = "outgoing"


class Source(Arguments):
    file: str = Field(min_length=1, max_length=500)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class Paths(Arguments):
    source_id: str
    target_id: str
    relationship: Literal["calls", "imports"] = "calls"


TOOL_MODELS = {
    "search_symbols": (
        Search,
        "Search names, docstrings and source text. Returns at most eight candidates.",
    ),
    "get_node": (
        NodeLookup,
        "Read a symbol and a cited source excerpt from the analyzed snapshot.",
    ),
    "get_neighbors": (
        Neighbors,
        "Read direct callers/callees, importers/imports or containment. Incomplete static evidence.",
    ),
    "read_source": (
        Source,
        "Read up to 80 lines of an analyzed Python file, with a citation. No arbitrary filesystem access.",
    ),
    "find_paths": (
        Paths,
        "Find one directed path of at most eight edges; absence does not prove no dependency.",
    ),
}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": model.model_json_schema(),
        },
    }
    for name, (model, desc) in TOOL_MODELS.items()
]


def words(text):
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return set(re.findall(r"[a-z0-9]+", text.lower()))


class Evidence:
    def __init__(self, analysis: Analysis):
        self.analysis = analysis
        self.nodes = {n["id"]: n for n in analysis.graph["nodes"]}
        self.edges = analysis.graph["edges"]
        self.citations: dict[str, dict] = {}
        self._ranges = {}
        self._search_index = []
        source_lines = {
            file: source.splitlines() for file, source in analysis.sources.items()
        }
        for node in self.nodes.values():
            source = source_lines.get(node["file"], [])
            excerpt = "\n".join(
                source[
                    node["lineno"] - 1 : min(node["end_lineno"], node["lineno"] + 79)
                ]
            )
            names = node["qualified_name"] + (
                " entry entrypoint points main" if node.get("is_entrypoint") else ""
            )
            self._search_index.append(
                (
                    node,
                    words(names),
                    words((node.get("docstring") or "") + " " + excerpt),
                )
            )

    def search_symbols(self, query):
        stop = {
            "what",
            "where",
            "does",
            "the",
            "this",
            "how",
            "is",
            "a",
            "an",
            "to",
            "of",
            "in",
            "it",
            "and",
            "for",
            "me",
            "explain",
            "code",
            "function",
            "happen",
        }
        terms = words(query) - stop
        ranked = []
        for node, names, body in self._search_index:
            score = 5 * len(terms & names) + len(terms & body)
            if query.lower() in node["qualified_name"].lower():
                score += 10
            if score:
                ranked.append((score, node["id"], node))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        return {
            "matches": [self.summary(n) for _, _, n in ranked[:8]],
            "truncated": len(ranked) > 8,
        }

    @staticmethod
    def summary(node):
        return {
            k: node[k]
            for k in ("id", "type", "qualified_name", "file", "lineno", "end_lineno")
        }

    def read_source(self, file, start_line, end_line):
        if file not in self.analysis.sources:
            raise ValueError("File is not part of this analysis.")
        lines = self.analysis.sources[file].splitlines() or [""]
        if end_line < start_line or start_line > len(lines):
            raise ValueError("Invalid source line range.")
        end = min(end_line, len(lines), start_line + 79)
        # Bound characters too, including files with huge individual lines.
        selected, size = [], 0
        for line in lines[start_line - 1 : end]:
            remaining = 6000 - size
            if remaining <= 0:
                break
            selected.append(line[:remaining])
            size += len(selected[-1]) + 1
        actual_end = start_line + len(selected) - 1
        truncated = actual_end < end_line or selected[-1] != lines[actual_end - 1]
        key = (file, start_line, actual_end)
        if key not in self._ranges:
            if len(self.citations) >= 24:
                raise ValueError(
                    "Evidence budget reached; answer from existing citations."
                )
            cid = f"S{len(self.citations) + 1}"
            # Prefer the narrowest symbol containing this range.
            owners = sorted(
                (
                    n
                    for n in self.nodes.values()
                    if n["file"] == file
                    and n["lineno"] <= start_line
                    and n["end_lineno"] >= actual_end
                ),
                key=lambda n: n["end_lineno"] - n["lineno"],
            )
            self.citations[cid] = {
                "id": cid,
                "file": file,
                "start_line": start_line,
                "end_line": actual_end,
                "node_id": owners[0]["id"] if owners else None,
                "snippet": "\n".join(selected),
                "truncated": truncated,
            }
            self._ranges[key] = cid
        cid = self._ranges[key]
        return dict(self.citations[cid])

    def get_node(self, node_id):
        if node_id not in self.nodes:
            raise ValueError("Unknown node id.")
        node = self.nodes[node_id]
        excerpt = self.read_source(node["file"], node["lineno"], node["end_lineno"])
        # A full symbol excerpt should highlight that exact symbol.
        self.citations[excerpt["id"]]["node_id"] = node_id
        return {"node": self.summary(node), "source": self.citations[excerpt["id"]]}

    def get_neighbors(self, node_id, relationship="calls", direction="outgoing"):
        if node_id not in self.nodes:
            raise ValueError("Unknown node id.")
        anchor, other = (
            ("source", "target") if direction == "outgoing" else ("target", "source")
        )
        matching = [
            e for e in self.edges if e["type"] == relationship and e[anchor] == node_id
        ]
        return {
            "edges": matching[:8],
            "nodes": [
                self.get_node(nid)
                for nid in dict.fromkeys(e[other] for e in matching[:8])
            ],
            "truncated": len(matching) > 8,
            "note": "Static relationships are incomplete; unknown dynamic calls are not represented.",
        }

    def find_paths(self, source_id, target_id, relationship="calls"):
        if source_id not in self.nodes or target_id not in self.nodes:
            raise ValueError("Unknown endpoint.")
        adjacency = {}
        for edge in self.edges:
            if edge["type"] == relationship:
                adjacency.setdefault(edge["source"], []).append(edge["target"])
        queue, seen = deque([(source_id, [source_id])]), {source_id}
        while queue:
            current, path = queue.popleft()
            if current == target_id:
                return {"path": path, "nodes": [self.get_node(nid) for nid in path]}
            if len(path) > 8:
                continue
            for neighbor in adjacency.get(current, []):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        return {
            "path": [],
            "note": "No path found within eight edges in the incomplete static graph.",
        }

    def dispatch(self, name, arguments):
        if name not in TOOL_MODELS:
            raise ValueError("Unknown tool.")
        validated = TOOL_MODELS[name][0].model_validate(arguments)
        return getattr(self, name)(**validated.model_dump())
