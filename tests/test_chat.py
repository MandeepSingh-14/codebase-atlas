from types import SimpleNamespace as NS

import pytest
from codemap.api import app
from codemap.graph.builder import serialize_for_frontend
from codemap.knowledge import Analysis, Evidence
from codemap.llm import chat
from codemap.parser.ast_parser import parse_repo
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def analysis(parsed_sample):
    return Analysis(
        "sample", serialize_for_frontend(parsed_sample), parsed_sample.sources
    )


def response(content=None, calls=()):
    return NS(choices=[NS(message=NS(content=content, tool_calls=list(calls)))])


def call(name, arguments, ident="call_1"):
    return NS(id=ident, function=NS(name=name, arguments=arguments))


def test_local_chat_has_real_citations(analysis):
    result = chat.ask(analysis, "Calculator compute")
    assert result["source"] == "fallback"
    assert result["fallback_reason"] == "not_configured"
    assert result["citations"]
    for c in result["citations"]:
        actual = analysis.sources[c["file"]].splitlines()[
            c["start_line"] - 1 : c["end_line"]
        ]
        assert c["snippet"] == "\n".join(actual)
        assert c["node_id"] in result["highlight_node_ids"]


def test_unknown_query_does_not_invent_evidence(analysis):
    result = chat.ask(analysis, "xyzzyfoobar")
    assert not result["citations"]
    assert "could not find" in result["answer"]


def test_tools_search_read_neighbors_and_paths(analysis):
    e = Evidence(analysis)
    assert e.search_symbols("compute")["matches"][0]["id"] == "core.Calculator.compute"
    result = e.get_neighbors("core.Calculator.compute")
    assert any(n["node"]["id"] == "util.helpers.add" for n in result["nodes"])
    assert e.find_paths("app", "util.helpers", "imports")["path"] == [
        "app",
        "core",
        "util.helpers",
    ]
    assert not e.find_paths("util.helpers", "app", "imports")["path"]


def test_source_tool_rejects_arbitrary_paths_and_ranges(analysis):
    e = Evidence(analysis)
    for path in ("../.env", "C:/secrets.py", ".env"):
        with pytest.raises(ValueError):
            e.read_source(path, 1, 5)
    with pytest.raises(ValueError):
        e.read_source("core.py", 999, 1000)
    with pytest.raises(ValueError):
        e.dispatch("read_source", {"file": "core.py", "start_line": -1, "end_line": 8})
    with pytest.raises(ValueError):
        e.dispatch("get_node", {"node_id": "core", "extra": "no"})
    with pytest.raises(ValueError):
        e.dispatch("execute_shell", {})


def test_source_excerpt_limits():
    e = Evidence(
        Analysis(
            "x", {"nodes": [], "edges": []}, {"x.py": "\n".join(["x" * 1000] * 200)}
        )
    )
    result = e.read_source("x.py", 1, 200)
    assert len(result["snippet"]) <= 6000
    assert result["end_line"] < 200 and result["truncated"]


def test_llm_tool_round_trip_and_citation_validation(analysis, monkeypatch):
    monkeypatch.setattr(chat, "llm_available", lambda: True)
    replies = iter(
        [
            response(calls=[call("get_node", '{"node_id":"util.helpers.add"}')]),
            response(
                '{"answer":"This helper adds two numbers [S1].", "citation_ids":["S1"]}'
            ),
        ]
    )
    requests = []

    def fake(**kwargs):
        requests.append(list(kwargs["messages"]))
        return next(replies)

    monkeypatch.setattr(chat, "complete", fake)
    result = chat.ask(analysis, "xyzzyfoobar")
    assert result["source"] == "llm"
    assert result["citations"][0]["node_id"] == "util.helpers.add"
    assert requests[1][-1]["role"] == "tool"
    assert "snippet" in requests[1][-1]["content"]


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        '{"answer":"Made up [S999]", "citation_ids":["S999"]}',
        '{"answer":"No references", "citation_ids":[]}',
        '{"answer":"Claim [S1]", "citation_ids":["S2"]}',
        '{"answer":null, "citation_ids":[]}',
    ],
)
def test_unverified_answers_fall_back(analysis, monkeypatch, content):
    monkeypatch.setattr(chat, "llm_available", lambda: True)
    monkeypatch.setattr(chat, "complete", lambda **kwargs: response(content))
    result = chat.ask(analysis, "compute")
    assert result["source"] == "fallback"
    assert result["fallback_reason"] == "invalid_response"
    assert "Made up" not in result["answer"]


def test_provider_failure_preserves_local_evidence(analysis, monkeypatch):
    monkeypatch.setattr(chat, "llm_available", lambda: True)

    def fail(**kwargs):
        raise TimeoutError("private provider details")

    monkeypatch.setattr(chat, "complete", fail)
    result = chat.ask(analysis, "compute")
    assert result["fallback_reason"] == "provider_error"
    assert result["citations"] and "private" not in str(result)


def test_bad_tool_arguments_are_returned_to_model(analysis, monkeypatch):
    monkeypatch.setattr(chat, "llm_available", lambda: True)
    replies = iter(
        [response(calls=[call("read_source", "{bad json")]), response("invalid")]
    )
    requests = []

    def fake(**kwargs):
        requests.append(list(kwargs["messages"]))
        return next(replies)

    monkeypatch.setattr(chat, "complete", fake)
    chat.ask(analysis, "compute")
    assert "error" in requests[1][-1]["content"]


def test_tool_budget_is_bounded(analysis, monkeypatch):
    monkeypatch.setattr(chat, "llm_available", lambda: True)
    monkeypatch.setattr(
        chat,
        "complete",
        lambda **kwargs: response(
            calls=[call("search_symbols", '{"query":"compute"}')] * 13
        ),
    )
    assert chat.ask(analysis, "compute")["fallback_reason"] == "budget_exhausted"


def test_analysis_ids_and_source_snapshots_isolate_tabs(tmp_path):
    client = TestClient(app)
    (tmp_path / "demo.py").write_text("def run():\n    return 1\n")
    first = client.post("/api/analyze", json={"path": str(tmp_path)}).json()
    (tmp_path / "demo.py").write_text("def run():\n    return 2\n")
    second = client.post("/api/analyze", json={"path": str(tmp_path)}).json()
    assert first["analysis_id"] != second["analysis_id"]
    for payload, number in ((first, 1), (second, 2)):
        r = client.post(
            "/api/chat", json={"analysis_id": payload["analysis_id"], "question": "run"}
        )
        assert r.status_code == 200
        assert any(f"return {number}" in c["snippet"] for c in r.json()["citations"])
        r = client.get(
            "/api/source",
            params={"analysis_id": payload["analysis_id"], "node_id": "demo.run"},
        )
        assert f"return {number}" in r.json()["snippet"]
    r = client.post("/api/chat", json={"analysis_id": "missing", "question": "run"})
    assert r.status_code == 404
    r = client.post(
        "/api/chat",
        json={
            "analysis_id": first["analysis_id"],
            "question": " ",
            "selected_node_id": "demo.run",
        },
    )
    assert r.status_code == 422
    r = client.post(
        "/api/chat",
        json={
            "analysis_id": first["analysis_id"],
            "question": "run",
            "selected_node_id": "missing",
        },
    )
    assert r.status_code == 404


def test_cache_eviction_returns_actionable_error(tmp_path, monkeypatch):
    import codemap.api as api

    monkeypatch.setattr(api, "MAX_ANALYSES", 2)
    monkeypatch.setattr(api, "_analyses", api.OrderedDict())
    client = TestClient(app)
    (tmp_path / "demo.py").write_text("x = 1")
    ids = [
        client.post("/api/analyze", json={"path": str(tmp_path)}).json()["analysis_id"]
        for _ in range(3)
    ]
    assert len(api._analyses) == 2
    result = client.post("/api/chat", json={"analysis_id": ids[0], "question": "x"})
    assert result.status_code == 404 and "again" in result.json()["detail"]


def test_source_html_is_returned_as_data(tmp_path):
    (tmp_path / "demo.py").write_text(
        'def f():\n    """<img src=x onerror=alert(1)>"""\n    pass'
    )
    parsed = parse_repo(tmp_path)
    a = Analysis("x", serialize_for_frontend(parsed), parsed.sources)
    assert "<img" in chat.ask(a, "f")["citations"][0]["snippet"]


def test_entry_point_search_uses_metadata(analysis):
    result = Evidence(analysis).search_symbols('Where are the entry points?')
    assert result['matches'][0]['id'] in ('app', 'app.main')
