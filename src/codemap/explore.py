"""Deterministic exploration, impact hints and snapshot comparisons."""

import hashlib
from collections import deque

import networkx as nx

LIMITATION = "Potential dependencies from static analysis, not an exhaustive runtime impact guarantee."


def walk(
    graph, node_id, direction="incoming", relationship="calls", depth=3, limit=200
):
    nodes = {n["id"]: n for n in graph["nodes"]}
    if node_id not in nodes:
        raise ValueError("Unknown node.")
    anchor, other = (
        ("target", "source") if direction == "incoming" else ("source", "target")
    )
    adjacency = {}
    for edge in graph["edges"]:
        if edge["type"] == relationship:
            adjacency.setdefault(edge[anchor], []).append(edge)
    queue = deque([(node_id, 0)])
    distances, selected, truncated = {node_id: 0}, {}, False
    while queue:
        current, level = queue.popleft()
        if level == depth:
            if any(e[other] not in distances for e in adjacency.get(current, [])):
                truncated = True
            continue
        for edge in adjacency.get(current, []):
            target = edge[other]
            if target not in distances:
                if len(distances) >= limit:
                    truncated = True
                    continue
                distances[target] = level + 1
                queue.append((target, level + 1))
            selected[(edge["source"], edge["target"], edge["type"])] = edge
    return {
        "nodes": [
            {**nodes[nid], "distance": distance} for nid, distance in distances.items()
        ],
        "edges": list(selected.values()),
        "truncated": truncated,
        "limitation": LIMITATION,
    }


def impact(graph, node_id, depth=3):
    result = walk(graph, node_id, depth=depth)
    nodes = {n["id"]: n for n in graph["nodes"]}
    node = nodes[node_id]
    modules = [
        n for n in nodes.values() if n["file"] == node["file"] and n["type"] == "module"
    ]
    importers = (
        walk(graph, modules[0]["id"], relationship="imports", depth=depth)
        if modules
        else {"nodes": [], "edges": [], "truncated": False}
    )
    # Keep importer evidence separate: importing a file does not prove calling a symbol.
    files = {n["file"] for n in result["nodes"] + importers["nodes"]}
    tests = [
        n
        for n in nodes.values()
        if n["file"] in files
        and (
            n["file"].split("/")[-1].startswith("test_") or "/tests/" in "/" + n["file"]
        )
        and n["type"] in ("function", "method")
        and n["name"].startswith("test_")
    ]
    return {
        **result,
        "importers": importers,
        "related_tests": tests[:40],
        "test_note": "Tests in dependent files are inspection candidates; coverage is not proven.",
    }


def overview(graph):
    module_graph = nx.DiGraph()
    module_graph.add_nodes_from(
        n["id"] for n in graph["nodes"] if n["type"] == "module"
    )
    module_graph.add_edges_from(
        (e["source"], e["target"]) for e in graph["edges"] if e["type"] == "imports"
    )
    cycles = [
        sorted(group)
        for group in nx.strongly_connected_components(module_graph)
        if len(group) > 1
    ]
    callers = {}
    for edge in graph["edges"]:
        if edge["type"] == "calls":
            callers.setdefault(edge["target"], set()).add(edge["source"])
    ranked = sorted(
        ({"node_id": nid, "callers": len(ids)} for nid, ids in callers.items()),
        key=lambda item: (-item["callers"], item["node_id"]),
    )
    return {
        "entrypoints": graph["entrypoints"],
        "most_called": ranked[:10],
        "import_cycles": sorted(cycles)[:20],
        "cycle_count": len(cycles),
        "limitation": LIMITATION,
    }


def compare(before, after):
    if before.graph.get("repository_id") != after.graph.get("repository_id"):
        raise ValueError("Compare two analyses of the same repository.")
    old = {n["id"]: n for n in before.graph["nodes"]}
    new = {n["id"]: n for n in after.graph["nodes"]}

    def digest(analysis, node):
        lines = analysis.sources[node["file"]].splitlines()
        return hashlib.sha256(
            "\n".join(lines[node["lineno"] - 1 : node["end_lineno"]]).encode()
        ).hexdigest()

    changed = sorted(
        nid
        for nid in old.keys() & new.keys()
        if digest(before, old[nid]) != digest(after, new[nid])
    )
    old_edges = {(e["source"], e["target"], e["type"]) for e in before.graph["edges"]}
    new_edges = {(e["source"], e["target"], e["type"]) for e in after.graph["edges"]}
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": changed,
        "edges_added": [
            dict(zip(("source", "target", "type"), e))
            for e in sorted(new_edges - old_edges)
        ],
        "edges_removed": [
            dict(zip(("source", "target", "type"), e))
            for e in sorted(old_edges - new_edges)
        ],
        "before_id": before.id,
        "after_id": after.id,
    }
