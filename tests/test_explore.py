from codemap.api import app
from codemap.explore import walk
from fastapi.testclient import TestClient

client = TestClient(app)


def analyze(tmp_path, text):
    (tmp_path / "app.py").write_text(text)
    r = client.post("/api/analyze", json={"path": str(tmp_path)})
    assert r.status_code == 200
    return r.json()


def test_every_edge_retains_source_locations(tmp_path):
    (tmp_path / "util.py").write_text("def work(): pass\n")
    graph = analyze(
        tmp_path, "from util import work\ndef main():\n    work()\n    work()\n"
    )
    for edge in graph["edges"]:
        assert edge["evidence"]
        for site in edge["evidence"]:
            assert (tmp_path / site["file"]).is_file()
            assert site["lineno"] >= 1
    edge = next(e for e in graph["edges"] if e["type"] == "calls")
    assert [s["lineno"] for s in edge["evidence"]] == [3, 4]
    result = client.get(
        "/api/relationship",
        params={
            "analysis_id": graph["analysis_id"],
            "source_id": "app.main",
            "target_id": "util.work",
            "relationship": "calls",
        },
    )
    assert result.status_code == 200
    assert len(result.json()["excerpts"]) == 2
    assert all("work()" in c["snippet"] for c in result.json()["excerpts"])


def test_directional_impact_and_flow(tmp_path):
    g = analyze(
        tmp_path, "def leaf(): pass\ndef middle(): leaf()\ndef main(): middle()\n"
    )
    for mode, node, expected in [
        ("impact", "app.leaf", {"app.leaf", "app.middle", "app.main"}),
        ("flow", "app.main", {"app.main", "app.middle", "app.leaf"}),
    ]:
        r = client.get(
            "/api/explore",
            params={"analysis_id": g["analysis_id"], "node_id": node, "mode": mode},
        )
        assert r.status_code == 200
        assert {n["id"] for n in r.json()["nodes"]} == expected
    r = client.get(
        "/api/explore",
        params={"analysis_id": g["analysis_id"], "node_id": "app.leaf", "depth": 1},
    )
    assert r.json()["truncated"]
    assert {n["id"] for n in r.json()["nodes"]} == {"app.leaf", "app.middle"}
    assert (
        client.get(
            "/api/explore",
            params={
                "analysis_id": g["analysis_id"],
                "node_id": "app.leaf",
                "depth": 100,
            },
        ).status_code
        == 422
    )


def test_importers_are_distinct_from_callers(tmp_path):
    (tmp_path / "other.py").write_text("import app\n")
    g = analyze(tmp_path, "def work(): pass\n")
    r = client.get(
        "/api/explore", params={"analysis_id": g["analysis_id"], "node_id": "app.work"}
    ).json()
    assert [n["id"] for n in r["nodes"]] == ["app.work"]
    assert "other" in {n["id"] for n in r["importers"]["nodes"]}


def test_path_endpoint_and_no_path(tmp_path):
    g = analyze(tmp_path, "def a(): pass\ndef b(): a()\n")
    args = {"analysis_id": g["analysis_id"], "source_id": "app.b", "target_id": "app.a"}
    assert client.get("/api/path", params=args).json()["path"] == ["app.b", "app.a"]
    args["source_id"], args["target_id"] = "app.a", "app.b"
    assert client.get("/api/path", params=args).json()["path"] == []
    args["target_id"] = "unknown"
    assert client.get("/api/path", params=args).status_code == 404


def test_compare_actual_source_and_relationship_changes(tmp_path):
    old = analyze(tmp_path, "def a(): return 1\ndef removed(): pass\n")
    new = analyze(tmp_path, "def a(): return 2\ndef added(): a()\n")
    r = client.get(
        "/api/compare",
        params={"before_id": old["analysis_id"], "after_id": new["analysis_id"]},
    ).json()
    assert r["added"] == ["app.added"] and r["removed"] == ["app.removed"]
    assert "app.a" in r["changed"]
    assert any(
        e["source"] == "app.added" and e["target"] == "app.a" for e in r["edges_added"]
    )
    same = client.get(
        "/api/compare",
        params={"before_id": new["analysis_id"], "after_id": new["analysis_id"]},
    ).json()
    assert not same["changed"] and not same["added"] and not same["removed"]


def test_compare_rejects_different_repositories(tmp_path):
    first = analyze(tmp_path, "x=1\n")
    second_dir = tmp_path / "sub"
    second_dir.mkdir()
    second = analyze(second_dir, "x=2\n")
    r = client.get(
        "/api/compare",
        params={"before_id": first["analysis_id"], "after_id": second["analysis_id"]},
    )
    assert r.status_code == 422


def test_cycle_detection_and_resolved_caller_counts(tmp_path):
    (tmp_path / "other.py").write_text("import app\n")
    g = analyze(tmp_path, "import other\ndef a(): pass\ndef b(): a()\n")
    assert g["overview"]["import_cycles"] == [["app", "other"]]
    assert g["overview"]["most_called"][0] == {"node_id": "app.a", "callers": 1}


def test_diagnostics_include_unknown_call_and_syntax_failure(tmp_path):
    (tmp_path / "broken.py").write_text("def nope(:")
    g = analyze(tmp_path, "def main(client): client.save()\n")
    assert any(
        d["target"] == "client.save" and d["lineno"] == 1
        for d in g["diagnostics"]
        if d["kind"] == "unresolved_call"
    )
    assert any(
        d["kind"] == "file_error" and d["file"] == "broken.py" for d in g["diagnostics"]
    )


def test_cycle_walk_stops_and_node_limit_applies():
    graph = {
        "nodes": [{"id": str(i)} for i in range(10)],
        "edges": [
            {"source": str(i), "target": str((i + 1) % 10), "type": "calls"}
            for i in range(10)
        ],
    }
    result = walk(graph, "0", depth=20, limit=4)
    assert len(result["nodes"]) == 4 and result["truncated"]


def test_analysis_list_never_contains_source_or_credentials(tmp_path):
    graph = analyze(tmp_path, 'SECRET_VALUE="fixture-only"\n')
    r = client.get("/api/analyses")
    assert graph["analysis_id"] in {x["analysis_id"] for x in r.json()}
    assert "SECRET_VALUE" not in r.text and "OPENAI_API_KEY" not in r.text


def test_frontend_assets_are_served_locally():
    page=client.get('/')
    assert '/static/vendor/cytoscape.min.js' in page.text
    assert 'https://cdnjs' not in page.text
    library=client.get('/static/vendor/cytoscape.min.js')
    assert library.status_code==200 and len(library.content)>100000
    assert client.get('/static/vendor/LICENSE.cytoscape').status_code==200
