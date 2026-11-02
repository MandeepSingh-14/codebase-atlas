"""Run the structural-analysis eval harness over hand-labeled repos and
print real accuracy numbers.

Usage:
    uv run python evals/run_evals.py [--labels evals/labels.yaml]

This is the honesty layer: it proves the graph's structural claims (entry
points, most-central node) against human labels, producing precision/
recall/F1 and top-k accuracy instead of an unverified "it works".
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from codemap.parser.ast_parser import parse_repo
from codemap.graph.builder import (
    build_graph,
    degree_centrality_ranking,
    entrypoint_ids,
    most_central_node,
)
from codemap.evals.metrics import evaluate_repo

PROJECT_ROOT = Path(__file__).parent.parent


def run(labels_path: Path) -> dict:
    labels = yaml.safe_load(labels_path.read_text())
    reports = []

    for repo in labels["repos"]:
        repo_path = (PROJECT_ROOT / repo["path"]).resolve()
        parsed = parse_repo(repo_path)
        g = build_graph(parsed)
        ranking_ids = [nid for nid, _ in degree_centrality_ranking(g)]

        report = evaluate_repo(
            detected_entrypoints=entrypoint_ids(parsed),
            expected_entrypoints=repo.get("expected_entrypoints", []),
            centrality_ranking_ids=ranking_ids,
            expected_most_central=repo.get("expected_most_central"),
        )
        report["repo"] = repo["name"]
        report["detected_most_central"] = most_central_node(g)
        reports.append(report)

    # Aggregate entry-point F1 across repos (macro average).
    f1s = [r["entrypoint_detection"]["f1"] for r in reports]
    top1 = [r["most_central_top1"] for r in reports if "most_central_top1" in r]

    summary = {
        "repos_evaluated": len(reports),
        "mean_entrypoint_f1": round(sum(f1s) / len(f1s), 3) if f1s else None,
        "most_central_top1_accuracy": round(sum(top1) / len(top1), 3) if top1 else None,
        "per_repo": reports,
    }
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default=str(PROJECT_ROOT / "evals" / "labels.yaml"))
    args = parser.parse_args()

    summary = run(Path(args.labels))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
