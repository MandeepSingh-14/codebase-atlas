"""Local codebase analysis, source snapshots, narration and grounded chat API."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from codemap import explore
from codemap.graph.builder import serialize_for_frontend
from codemap.knowledge import Analysis, Evidence
from codemap.llm.chat import ask, configured_model
from codemap.llm.narrator import explain_node, llm_available
from codemap.parser.ast_parser import parse_repo

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
app = FastAPI(title="Codebase Cartographer", version="0.2.0")
STATIC_DIR = Path(__file__).parent / "static"
# Bounded, single-process local cache; analysis IDs isolate browser tabs.
MAX_ANALYSES = 8
_analyses: OrderedDict[str, Analysis] = OrderedDict()
_cache_lock = Lock()
_last_analysis_id: str | None = None


class AnalyzeRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    layout_seed: int = 42


class ExplainRequest(BaseModel):
    node_id: str
    analysis_id: str | None = None  # Backward compatibility for the original API.


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(BaseModel):
    analysis_id: str
    question: str = Field(min_length=1, max_length=2000)
    selected_node_id: str | None = None
    history: list[Turn] = Field(default_factory=list, max_length=8)


def get_analysis(analysis_id: str | None) -> Analysis:
    with _cache_lock:
        analysis = _analyses.get(analysis_id)
        if analysis is None:
            raise HTTPException(
                404, "Analysis expired or unknown. Analyze the repository again."
            )
        _analyses.move_to_end(analysis.id)
        return analysis


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "llm_available": llm_available(),
        "model": configured_model(),
    }


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    cleaned = req.path.strip().strip('"').strip("'").strip()
    root = Path(cleaned).expanduser()
    if not cleaned or not root.is_dir():
        raise HTTPException(400, f"Not a directory: {cleaned}")
    parsed = parse_repo(root)
    if parsed.files_parsed == 0:
        raise HTTPException(422, "No parseable Python files found under that path.")
    payload = serialize_for_frontend(parsed, layout_seed=req.layout_seed)
    analysis_id = uuid4().hex
    payload["analysis_id"] = analysis_id
    payload["repository_id"] = hashlib.sha256(
        str(root.resolve()).casefold().encode()
    ).hexdigest()
    payload["repository_name"] = root.resolve().name
    payload["created_at"] = datetime.now(timezone.utc).isoformat()
    payload["diagnostics"] = parsed.diagnostics[:1000]
    payload["overview"] = explore.overview(payload)
    analysis = Analysis(analysis_id, payload, parsed.sources)
    global _last_analysis_id
    with _cache_lock:
        _analyses[analysis_id] = analysis
        _last_analysis_id = analysis_id
        while len(_analyses) > MAX_ANALYSES:
            _analyses.popitem(last=False)
    return payload


@app.post("/api/chat")
def chat(req: ChatRequest):
    analysis = get_analysis(req.analysis_id)
    if not req.question.strip():
        raise HTTPException(422, "Question cannot be blank.")
    if req.selected_node_id and not any(
        n["id"] == req.selected_node_id for n in analysis.graph["nodes"]
    ):
        raise HTTPException(404, "Unknown selected node.")
    return {
        "analysis_id": analysis.id,
        **ask(
            analysis,
            req.question.strip(),
            req.selected_node_id,
            [turn.model_dump() for turn in req.history],
        ),
    }


@app.get("/api/source")
def source(analysis_id: str, node_id: str, start_line: int | None = None):
    evidence = Evidence(get_analysis(analysis_id))
    if node_id not in evidence.nodes:
        raise HTTPException(404, "Unknown node.")
    node = evidence.nodes[node_id]
    start = node["lineno"] if start_line is None else start_line
    if start < node["lineno"] or start > node["end_lineno"]:
        raise HTTPException(422, "Line must be inside the selected symbol.")
    return evidence.read_source(
        node["file"], start, min(node["end_lineno"], start + 79)
    )


@app.post("/api/explain")
def explain(req: ExplainRequest):
    analysis = get_analysis(req.analysis_id or _last_analysis_id)
    nodes = {n["id"]: n for n in analysis.graph["nodes"]}
    if req.node_id not in nodes:
        raise HTTPException(404, "Unknown node id (analyze a repo first).")
    neighbor_ids = dict.fromkeys(
        e["target"] if e["source"] == req.node_id else e["source"]
        for e in analysis.graph["edges"]
        if req.node_id in (e["source"], e["target"])
    )
    return {
        "node_id": req.node_id,
        **explain_node(
            nodes[req.node_id],
            [nodes[nid] for nid in neighbor_ids if nid in nodes],
            model=configured_model(),
        ),
    }


@app.get("/api/explore")
def explore_node(
    analysis_id: str,
    node_id: str,
    mode: Literal["impact", "flow"] = "impact",
    depth: int = 3,
):
    if not 1 <= depth <= 6:
        raise HTTPException(422, "Depth must be between one and six.")
    analysis = get_analysis(analysis_id)
    try:
        return (
            explore.impact(analysis.graph, node_id, depth)
            if mode == "impact"
            else explore.walk(
                analysis.graph, node_id, direction="outgoing", depth=depth
            )
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/relationship")
def relationship(
    analysis_id: str,
    source_id: str,
    target_id: str,
    relationship: Literal["calls", "imports", "contains"],
):
    analysis = get_analysis(analysis_id)
    edge = next(
        (
            e
            for e in analysis.graph["edges"]
            if e["source"] == source_id
            and e["target"] == target_id
            and e["type"] == relationship
        ),
        None,
    )
    if edge is None:
        raise HTTPException(404, "Unknown relationship.")
    evidence = Evidence(analysis)
    excerpts = [
        evidence.read_source(site["file"], site["lineno"], site["end_lineno"])
        for site in edge.get("evidence", [])[:20]
    ]
    return {
        "edge": edge,
        "excerpts": excerpts,
        "truncated": len(edge.get("evidence", [])) > 20,
    }


@app.get("/api/compare")
def compare_analyses(before_id: str, after_id: str):
    try:
        return explore.compare(get_analysis(before_id), get_analysis(after_id))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/path")
def call_path(analysis_id: str, source_id: str, target_id: str):
    try:
        return Evidence(get_analysis(analysis_id)).find_paths(source_id, target_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/analyses")
def analyses():
    with _cache_lock:
        return [
            {
                "analysis_id": a.id,
                "repository_id": a.graph["repository_id"],
                "repository_name": a.graph["repository_name"],
                "created_at": a.graph["created_at"],
            }
            for a in reversed(list(_analyses.values()))
        ]


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
