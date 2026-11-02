from codemap.parser.ast_parser import parse_repo


def parse(tmp_path, files):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf8")
    return parse_repo(tmp_path)


def calls(result):
    return {(e.source, e.target) for e in result.edges if e.type == "calls"}


def test_unrelated_method_is_not_guessed(tmp_path):
    r = parse(
        tmp_path,
        {"a.py": "def save(): pass", "b.py": "def run(client):\n    client.save()"},
    )
    assert not calls(r) and r.unresolved_calls == 1


def test_aliases_and_duplicate_names_resolve_by_scope(tmp_path):
    r = parse(
        tmp_path,
        {
            "a.py": "def save(): pass",
            "b.py": "def save(): pass",
            "c.py": "from a import save as persist\nimport b as mod\ndef run():\n    persist()\n    mod.save()",
        },
    )
    assert calls(r) == {("c.run", "a.save"), ("c.run", "b.save")}


def test_parameters_and_assignments_shadow_symbols(tmp_path):
    r = parse(
        tmp_path,
        {
            "a.py": "def save(): pass\ndef run(save):\n    save()\ndef other():\n    save = thing\n    save()"
        },
    )
    assert not calls(r) and r.unresolved_calls == 2


def test_nested_scope_and_recursion(tmp_path):
    r = parse(
        tmp_path,
        {
            "a.py": "def helper(): pass\ndef outer():\n    def inner():\n        helper()\n        inner()\n    inner()"
        },
    )
    assert "a.outer.inner" in {n.id for n in r.nodes}
    assert calls(r) == {
        ("a.outer.inner", "a.helper"),
        ("a.outer.inner", "a.outer.inner"),
        ("a.outer", "a.outer.inner"),
    }


def test_relative_imports_and_module_alias(tmp_path):
    r = parse(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from . import b\ndef run():\n    b.work()",
            "pkg/b.py": "def work(): pass",
        },
    )
    assert ("pkg.a.run", "pkg.b.work") in calls(r)
    assert any(
        e.source == "pkg.a" and e.target == "pkg.b" and e.type == "imports"
        for e in r.edges
    )


def test_external_suffix_does_not_create_internal_import(tmp_path):
    r = parse(
        tmp_path,
        {
            "helpers.py": "def work(): pass",
            "a.py": "from external.helpers import work\ndef run(): work()",
        },
    )
    assert not calls(r)
    assert not [e for e in r.edges if e.type == "imports"]


def test_entrypoint_guard_requires_exact_comparison(tmp_path):
    r = parse(
        tmp_path,
        {
            "a.py": 'if __name__ != "__main__": pass',
            "b.py": 'if "__main__" == __name__: pass',
            "c.py": "def outer():\n    def main(): pass",
        },
    )
    assert [n.id for n in r.nodes if n.is_entrypoint] == ["b"]


def test_class_members_are_not_lexical_method_locals(tmp_path):
    r = parse(
        tmp_path,
        {"a.py": "class A:\n    def save(self): pass\n    def run(self): save()"},
    )
    assert not calls(r)


def test_ignored_directories_and_python_encoding(tmp_path):
    parse(tmp_path, {".venv/hidden.py": "def hidden(): pass", "good.py": "x=1"})
    (tmp_path / "latin.py").write_bytes(b'# coding: latin-1\nvalue = "caf\xe9"\n')
    r = parse_repo(tmp_path)
    assert r.files_parsed == 2 and r.files_failed == 0
    assert "latin.py" in r.sources


def test_redefinitions_have_unique_display_ids_and_no_guessed_calls(tmp_path):
    r = parse(
        tmp_path, {"a.py": "def work(): pass\ndef work(): pass\ndef run(): work()"}
    )
    assert len(r.nodes) == len({n.id for n in r.nodes})
    assert not calls(r)


def test_src_layout_resolves_package_imports(tmp_path):
    r = parse(tmp_path, {'src/pkg/__init__.py':'', 'src/pkg/a.py':'from pkg.b import work\ndef run(): work()', 'src/pkg/b.py':'def work(): pass'})
    assert ('pkg.a.run', 'pkg.b.work') in calls(r)
    assert 'src/pkg/a.py' in r.sources


def test_analyzed_package_root_can_be_imported_by_name(tmp_path):
    root = tmp_path/'sample_pkg'
    root.mkdir()
    r = parse(root, {'__init__.py':'', 'a.py':'import sample_pkg.b\ndef run(): sample_pkg.b.work()', 'b.py':'def work(): pass'})
    assert ('a.run', 'b.work') in calls(r)
