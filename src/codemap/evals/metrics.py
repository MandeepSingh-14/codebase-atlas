"""Evaluation harness -- the differentiator.

The point: the structural analysis makes claims ("these are the entry
points", "this is the most central module"). Anyone can build a graph;
the question is whether the graph is *correct*. This module measures that
against hand-labeled ground truth, producing real precision/recall/accuracy
numbers instead of an unverified "it works" claim.

All functions here are pure (take data, return numbers) -- no parsing, no
network -- so they're trivially unit-testable.
"""

from __future__ import annotations


def precision_recall_f1(predicted: set[str], expected: set[str]) -> dict:
    """Standard set-based precision/recall/F1 for a detection task
    (e.g. entry-point detection)."""
    if not predicted and not expected:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "tp": 0, "fp": 0, "fn": 0}

    tp = len(predicted & expected)
    fp = len(predicted - expected)
    fn = len(expected - predicted)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def top_k_accuracy(ranked_ids: list[str], expected_id: str, k: int = 1) -> bool:
    """Did the expected node appear in the top-k of a ranking?
    Used for "is the most-central node the one a human would name?"."""
    return expected_id in ranked_ids[:k]


def evaluate_repo(
    detected_entrypoints: list[str],
    expected_entrypoints: list[str],
    centrality_ranking_ids: list[str],
    expected_most_central: str | None,
) -> dict:
    """Aggregate one repo's structural-analysis accuracy into a report."""
    report = {
        "entrypoint_detection": precision_recall_f1(
            set(detected_entrypoints), set(expected_entrypoints)
        ),
    }
    if expected_most_central is not None:
        report["most_central_top1"] = top_k_accuracy(
            centrality_ranking_ids, expected_most_central, k=1
        )
        report["most_central_top3"] = top_k_accuracy(
            centrality_ranking_ids, expected_most_central, k=3
        )
    return report
