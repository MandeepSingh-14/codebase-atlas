"""Tests for the evaluation metrics -- pure functions."""

from codemap.evals.metrics import precision_recall_f1, top_k_accuracy, evaluate_repo


def test_perfect_detection():
    r = precision_recall_f1({"a", "b"}, {"a", "b"})
    assert r["precision"] == 1.0 and r["recall"] == 1.0 and r["f1"] == 1.0


def test_both_empty_is_perfect():
    r = precision_recall_f1(set(), set())
    assert r["f1"] == 1.0


def test_false_positive_lowers_precision():
    r = precision_recall_f1({"a", "b", "c"}, {"a", "b"})
    assert r["tp"] == 2 and r["fp"] == 1 and r["fn"] == 0
    assert r["precision"] == round(2 / 3, 3)
    assert r["recall"] == 1.0


def test_missed_item_lowers_recall():
    r = precision_recall_f1({"a"}, {"a", "b"})
    assert r["fn"] == 1
    assert r["recall"] == 0.5


def test_top_k_accuracy():
    ranked = ["x", "y", "z"]
    assert top_k_accuracy(ranked, "x", k=1) is True
    assert top_k_accuracy(ranked, "y", k=1) is False
    assert top_k_accuracy(ranked, "y", k=3) is True


def test_evaluate_repo_full_report():
    report = evaluate_repo(
        detected_entrypoints=["app", "app.main"],
        expected_entrypoints=["app", "app.main"],
        centrality_ranking_ids=["core.Calculator", "app", "util.helpers.add"],
        expected_most_central="core.Calculator",
    )
    assert report["entrypoint_detection"]["f1"] == 1.0
    assert report["most_central_top1"] is True


def test_evaluate_repo_without_central_label():
    report = evaluate_repo(
        detected_entrypoints=["app"],
        expected_entrypoints=["app"],
        centrality_ranking_ids=["app"],
        expected_most_central=None,
    )
    assert "most_central_top1" not in report
