"""Tests for the networkx graph builder / layout / serialization."""

from codemap.graph.builder import (
    build_graph,
    compute_layout,
    degree_centrality_ranking,
    most_central_node,
    serialize_for_frontend,
    entrypoint_ids,
)


def test_graph_has_all_nodes(parsed_sample):
    g = build_graph(parsed_sample)
    assert g.number_of_nodes() == len(parsed_sample.nodes)


def test_external_import_edges_dropped(parsed_sample):
    # 'sample_pkg.util.helpers' isn't a node id (nodes use repo-relative
    # qnames like 'util.helpers'), so that import edge shouldn't be in the graph.
    g = build_graph(parsed_sample)
    for u, v in g.edges():
        assert u in g and v in g


def test_layout_deterministic(parsed_sample):
    l1 = compute_layout(build_graph(parsed_sample), layout_seed=42)
    l2 = compute_layout(build_graph(parsed_sample), layout_seed=42)
    assert l1 == l2  # same seed -> identical coordinates


def test_centrality_ranking_nonempty(parsed_sample):
    ranking = degree_centrality_ranking(build_graph(parsed_sample))
    assert len(ranking) == len(parsed_sample.nodes)
    # scores descending
    scores = [s for _, s in ranking]
    assert scores == sorted(scores, reverse=True)


def test_most_central_is_a_real_node(parsed_sample):
    mc = most_central_node(build_graph(parsed_sample))
    assert mc in {n.id for n in parsed_sample.nodes}


def test_entrypoint_ids(parsed_sample):
    eps = set(entrypoint_ids(parsed_sample))
    assert "app" in eps
    assert "app.main" in eps


def test_serialize_shape(parsed_sample):
    payload = serialize_for_frontend(parsed_sample)
    assert set(payload) == {"nodes", "edges", "stats", "entrypoints", "most_central"}
    for node in payload["nodes"]:
        assert "x" in node and "y" in node and "centrality" in node


def test_import_chain_is_connected(parsed_sample):
    # After import resolution the module import chain app -> core -> util.helpers
    # must exist as real edges in the graph (regression test for the
    # disconnected-modules bug: absolute import names weren't matching
    # relative node ids).
    g = build_graph(parsed_sample)
    assert g.has_edge("app", "core")
    assert g.has_edge("core", "util.helpers")


def test_empty_graph_handled():
    from codemap.parser.models import ParseResult
    payload = serialize_for_frontend(ParseResult())
    assert payload["nodes"] == []
    assert payload["most_central"] is None
