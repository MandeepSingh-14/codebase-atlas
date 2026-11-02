"""API tests via FastAPI TestClient -- no real LLM calls (no key in CI, so
narration falls back deterministically)."""

from pathlib import Path

from fastapi.testclient import TestClient

from codemap.api import app

client = TestClient(app)
SAMPLE = str(Path(__file__).parent / "fixtures" / "sample_pkg")


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_analyze_sample_pkg():
    r = client.post("/api/analyze", json={"path": SAMPLE})
    assert r.status_code == 200
    body = r.json()
    ids = {n["id"] for n in body["nodes"]}
    assert "core.Calculator" in ids
    assert "app" in body["entrypoints"]
    assert body["most_central"] in ids
    assert body["stats"]["files_parsed"] >= 4


def test_analyze_bad_path():
    r = client.post("/api/analyze", json={"path": "C:/definitely/not/real/xyz"})
    assert r.status_code == 400


def test_analyze_tolerates_pasted_quotes():
    # Users often paste a path with surrounding double-quotes from a file
    # explorer -- that should still work, not 400.
    r = client.post("/api/analyze", json={"path": f'  "{SAMPLE}"  '})
    assert r.status_code == 200


def test_explain_falls_back_without_key(monkeypatch):
    # Ensure no provider key is seen -> deterministic fallback explanation.
    for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    client.post("/api/analyze", json={"path": SAMPLE})
    r = client.post("/api/explain", json={"node_id": "core.Calculator"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "fallback"
    assert "Calculator" in body["explanation"]


def test_explain_unknown_node():
    client.post("/api/analyze", json={"path": SAMPLE})
    r = client.post("/api/explain", json={"node_id": "does.not.exist"})
    assert r.status_code == 404
