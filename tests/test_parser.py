"""Tests for the AST parser against the known fixture package."""

from codemap.parser.models import Node


def _ids(parsed):
    return {n.id for n in parsed.nodes}


def _by_id(parsed, node_id) -> Node:
    return next(n for n in parsed.nodes if n.id == node_id)


def test_all_modules_found(parsed_sample):
    ids = _ids(parsed_sample)
    # __init__.py files -> package modules; .py files -> modules
    assert "app" in ids
    assert "core" in ids
    assert "util.helpers" in ids


def test_classes_and_methods(parsed_sample):
    ids = _ids(parsed_sample)
    assert "core.Calculator" in ids
    assert "core.Calculator.compute" in ids
    assert "util.helpers.Formatter" in ids
    assert "util.helpers.Formatter.format_number" in ids

    assert _by_id(parsed_sample, "core.Calculator").type == "class"
    assert _by_id(parsed_sample, "core.Calculator.compute").type == "method"
    assert _by_id(parsed_sample, "util.helpers.add").type == "function"


def test_docstrings_extracted(parsed_sample):
    assert _by_id(parsed_sample, "util.helpers.add").docstring == "Add two numbers."
    assert "calculator" in _by_id(parsed_sample, "core.Calculator").docstring.lower()


def test_entrypoint_detection(parsed_sample):
    # app.py has both `def main()` and `if __name__ == "__main__"`.
    app_module = _by_id(parsed_sample, "app")
    main_fn = _by_id(parsed_sample, "app.main")
    assert app_module.is_entrypoint is True
    assert main_fn.is_entrypoint is True
    # helpers.py has neither -> not an entrypoint.
    assert _by_id(parsed_sample, "util.helpers").is_entrypoint is False


def test_contains_edges(parsed_sample):
    contains = {(e.source, e.target) for e in parsed_sample.edges if e.type == "contains"}
    assert ("core", "core.Calculator") in contains
    assert ("core.Calculator", "core.Calculator.compute") in contains
    assert ("util.helpers.Formatter", "util.helpers.Formatter.format_number") in contains


def test_import_edges_resolved_to_internal_ids(parsed_sample):
    # Absolute import targets (sample_pkg.core) are rewritten to the internal
    # relative module id (core), so intra-repo imports actually connect.
    imports = {(e.source, e.target) for e in parsed_sample.edges if e.type == "imports"}
    assert ("core", "util.helpers") in imports
    assert ("app", "core") in imports
    # The raw absolute names should no longer be present as targets.
    assert ("core", "sample_pkg.util.helpers") not in imports


def test_longest_suffix_match():
    from codemap.parser.ast_parser import _longest_suffix_match

    cands = {"util.helpers", "core", "helpers"}
    assert _longest_suffix_match("sample_pkg.util.helpers", cands) == "util.helpers"
    assert _longest_suffix_match("sample_pkg.core", cands) == "core"
    assert _longest_suffix_match("os.path", cands) is None  # external, no match


def test_call_edge_resolved_unambiguously(parsed_sample):
    # Calculator.compute calls add() -- 'add' is a unique short name, so it
    # should resolve to a single calls edge.
    calls = {(e.source, e.target) for e in parsed_sample.edges if e.type == "calls"}
    assert ("core.Calculator.compute", "util.helpers.add") in calls


def test_stats_populated(parsed_sample):
    assert parsed_sample.files_parsed >= 4
    assert parsed_sample.files_failed == 0


def test_syntax_error_file_is_counted_not_crashed(tmp_path):
    from codemap.parser.ast_parser import parse_repo

    (tmp_path / "good.py").write_text("def ok():\n    return 1\n")
    (tmp_path / "bad.py").write_text("def broken(:\n")  # syntax error
    result = parse_repo(tmp_path)
    assert result.files_parsed == 1
    assert result.files_failed == 1
