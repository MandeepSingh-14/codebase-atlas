"""Turn a ParseResult into a laid-out, ranked graph ready for the frontend.

Uses networkx for layout (deterministic, seeded) and degree centrality
(a cheap, explainable "how important is this node" signal). Centrality is
computed on the contains+calls structure so that heavily-connected modules
and functions float to the top -- this is what the eval harness measures
against hand labels.
"""

from __future__ import annotations

import networkx as nx

from codemap.parser.models import ParseResult


def build_graph(parse_result: ParseResult, layout_seed: int = 42) -> nx.DiGraph:
    g = nx.DiGraph()
    for node in parse_result.nodes:
        g.add_node(node.id, **node.to_dict())
    for edge in parse_result.edges:
        # Only add edges whose endpoints are real nodes (imports often point
        # at external modules like "os" that aren't nodes in this repo).
        if edge.source in g and edge.target in g:
            g.add_edge(edge.source, edge.target, type=edge.type)
    return g


def degree_centrality_ranking(g: nx.DiGraph) -> list[tuple[str, float]]:
    """Rank nodes by degree centrality, most central first."""
    if g.number_of_nodes() == 0:
        return []
    centrality = nx.degree_centrality(g)
    return sorted(centrality.items(), key=lambda kv: kv[1], reverse=True)


def most_central_node(g: nx.DiGraph) -> str | None:
    ranking = degree_centrality_ranking(g)
    return ranking[0][0] if ranking else None


def entrypoint_ids(parse_result: ParseResult) -> list[str]:
    return [n.id for n in parse_result.nodes if n.is_entrypoint]


def compute_layout(g: nx.DiGraph, layout_seed: int = 42) -> dict[str, dict[str, float]]:
    """2D coordinates per node id. Seeded so the same repo always lays out
    the same way (stable across runs -- important for a UI you revisit)."""
    if g.number_of_nodes() == 0:
        return {}
    # spring_layout on the undirected view gives a reasonable organic map.
    pos = nx.spring_layout(g.to_undirected(), seed=layout_seed, k=None)
    return {node_id: {"x": float(xy[0]), "y": float(xy[1])} for node_id, xy in pos.items()}


def serialize_for_frontend(parse_result: ParseResult, layout_seed: int = 42) -> dict:
    """Full payload the API returns and the frontend renders."""
    g = build_graph(parse_result, layout_seed=layout_seed)
    layout = compute_layout(g, layout_seed=layout_seed)
    centrality = dict(degree_centrality_ranking(g))

    nodes_out = []
    for node in parse_result.nodes:
        d = node.to_dict()
        pos = layout.get(node.id, {"x": 0.0, "y": 0.0})
        d["x"] = pos["x"]
        d["y"] = pos["y"]
        d["centrality"] = round(centrality.get(node.id, 0.0), 4)
        nodes_out.append(d)

    return {
        "nodes": nodes_out,
        "edges": [e.to_dict() for e in parse_result.edges if e.source in g and e.target in g],
        "stats": parse_result.to_dict()["stats"],
        "entrypoints": entrypoint_ids(parse_result),
        "most_central": most_central_node(g),
    }
