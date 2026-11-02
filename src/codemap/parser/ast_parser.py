"""Conservative, scope-aware Python structure extraction; never executes code."""

from __future__ import annotations

import ast
import os
import tokenize
from pathlib import Path

from codemap.parser.models import Edge, Node, ParseResult

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".eggs",
    "site-packages",
}


def _iter_python_files(root: Path) -> list[Path]:
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in SKIP_DIRS and not (Path(directory) / d).is_symlink()
        )
        files.extend(
            Path(directory) / n
            for n in names
            if n.endswith(".py") and not (Path(directory) / n).is_symlink()
        )
    return sorted(files)


def _module_qname(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).parts)
    if (
        len(parts) > 1
        and parts[0] == "src"
        and not (root / "src" / "__init__.py").exists()
    ):
        parts.pop(0)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(parts) if parts else "__init__"


def _longest_suffix_match(dotted: str, candidates: set[str]) -> str | None:
    # Compatibility helper; actual resolution below only strips a known root.
    parts = dotted.split(".")
    return next(
        (
            ".".join(parts[i:])
            for i in range(len(parts))
            if ".".join(parts[i:]) in candidates
        ),
        None,
    )


def _has_main_guard(module: ast.Module) -> bool:
    for stmt in module.body:
        test = stmt.test if isinstance(stmt, ast.If) else None
        if (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
        ):
            a, b = test.left, test.comparators[0]
            for left, right in ((a, b), (b, a)):
                if (
                    isinstance(left, ast.Name)
                    and left.id == "__name__"
                    and isinstance(right, ast.Constant)
                    and right.value == "__main__"
                ):
                    return True
    return False


def _dotted(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _dotted(expr.value)
        return f"{base}.{expr.attr}" if base else None
    return None


class _ModuleVisitor(ast.NodeVisitor):
    def __init__(self, name: str, rel: str, source: str, normalize):
        self.module = name
        self.rel = rel
        self.normalize = normalize
        self.nodes = [
            Node(
                name,
                "module",
                name.split(".")[-1],
                name,
                rel,
                1,
                max(1, len(source.splitlines())),
            )
        ]
        self.edges: list[Edge] = []
        self.calls: list[tuple[str, str | None, dict]] = []
        self.stack = [name]
        self.kinds = {name: "module"}
        self.parents: dict[str, str] = {}
        self.bindings: dict[str, dict[str, str | None]] = {name: {}}

    def location(self, node):
        return {
            "file": self.rel,
            "lineno": node.lineno,
            "end_lineno": getattr(node, "end_lineno", node.lineno) or node.lineno,
        }

    def bind(self, name: str, target: str | None):
        table = self.bindings[self.stack[-1]]
        # Rebinding is ambiguous without control-flow analysis; do not guess.
        table[name] = target if name not in table else None

    def visit_Module(self, node):
        self.nodes[0].docstring = ast.get_docstring(node)
        self.nodes[0].is_entrypoint = _has_main_guard(node)
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            target = self.normalize(alias.name)
            self.edges.append(
                Edge(self.module, target, "imports", [self.location(node)])
            )
            self.bind(
                alias.asname or alias.name.split(".")[0],
                target if alias.asname else self.normalize(alias.name.split(".")[0]),
            )

    def visit_ImportFrom(self, node):
        if node.level:
            package = (
                self.module.split(".")
                if self.rel.endswith("__init__.py")
                else self.module.split(".")[:-1]
            )
            if self.module == "__init__":
                package = []
            remove = node.level - 1
            if remove > len(package):
                return
            package = package[: len(package) - remove] if remove else package
            base = ".".join(package + ([node.module] if node.module else []))
        else:
            base = self.normalize(node.module or "")
        if node.module:
            self.edges.append(
                Edge(self.module, base or "__init__", "imports", [self.location(node)])
            )
        for alias in node.names:
            if alias.name == "*":
                continue
            target = ".".join(p for p in (base, alias.name) if p)
            self.bind(alias.asname or alias.name, target)
            # Also retain `from package import submodule` if it is a module.
            self.edges.append(
                Edge(self.module, target, "imports", [self.location(node)])
            )

    def definition(self, node, kind):
        parent = self.stack[-1]
        qname = f"{parent}.{node.name}"
        self.bind(node.name, qname)
        self.nodes.append(
            Node(
                qname,
                kind,
                node.name,
                qname,
                self.rel,
                node.lineno,
                node.end_lineno or node.lineno,
                ast.get_docstring(node),
                kind == "function" and parent == self.module and node.name == "main",
            )
        )
        self.edges.append(Edge(parent, qname, "contains", [self.location(node)]))
        self.kinds[qname] = kind
        self.parents[qname] = parent
        self.bindings[qname] = {}
        # Decorators, bases and defaults run in the enclosing scope.
        for expr in node.decorator_list:
            self.visit(expr)
        if isinstance(node, ast.ClassDef):
            for expr in node.bases:
                self.visit(expr)
        else:
            for expr in node.args.defaults + [x for x in node.args.kw_defaults if x]:
                self.visit(expr)
        self.stack.append(qname)
        if not isinstance(node, ast.ClassDef):
            args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            args += [x for x in (node.args.vararg, node.args.kwarg) if x]
            for arg in args:
                self.bind(arg.arg, None)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    def visit_ClassDef(self, node):
        self.definition(node, "class")

    def visit_FunctionDef(self, node):
        self.definition(
            node, "method" if self.kinds[self.stack[-1]] == "class" else "function"
        )

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id, None)

    def visit_Global(self, node):
        for name in node.names:
            self.bind(name, None)

    visit_Nonlocal = visit_Global

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bind(node.name, None)
        self.generic_visit(node)

    def visit_Call(self, node):
        self.calls.append((self.stack[-1], _dotted(node.func), self.location(node)))
        self.generic_visit(node)

    def visit_Lambda(self, node):
        # Anonymous callable scope is not modeled: count its calls as unknown.
        self.calls.extend(
            (self.stack[-1], None, self.location(x))
            for x in ast.walk(node)
            if isinstance(x, ast.Call)
        )

    def visit_ListComp(self, node):
        # Comprehensions have their own bindings; avoid resolving them as locals.
        self.calls.extend(
            (self.stack[-1], None, self.location(x))
            for x in ast.walk(node)
            if isinstance(x, ast.Call)
        )

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def resolve(self, caller: str, dotted: str | None) -> str | None:
        if not dotted:
            return None
        name, *attrs = dotted.split(".")
        scope = caller
        while scope:
            # Method bodies cannot access class members as lexical variables.
            if self.kinds[scope] != "class" or scope == caller:
                table = self.bindings[scope]
                if name in table:
                    target = table[name]
                    return (
                        ".".join(p for p in [target, *attrs] if p)
                        if target is not None
                        else None
                    )
            scope = self.parents.get(scope)
        return None


def parse_repo(root: str | Path) -> ParseResult:
    root = Path(root).resolve()
    result = ParseResult()
    visitors = []
    # Strip only the actual analyzed package root; never arbitrary suffixes.
    prefix = root.name + "." if (root / "__init__.py").is_file() else None

    def normalize(name):
        if prefix and name == root.name:
            return ""
        if prefix and name.startswith(prefix):
            return name[len(prefix) :]
        return name

    for path in _iter_python_files(root):
        try:
            with tokenize.open(path) as stream:
                source = stream.read()
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeError, OSError) as exc:
            result.diagnostics.append(
                {
                    "kind": "file_error",
                    "file": path.relative_to(root).as_posix(),
                    "lineno": getattr(exc, "lineno", 1),
                    "reason": type(exc).__name__,
                }
            )
            result.files_failed += 1
            continue
        rel = path.relative_to(root).as_posix()
        result.sources[rel] = source
        visitor = _ModuleVisitor(_module_qname(path, root), rel, source, normalize)
        visitor.visit(tree)
        visitors.append(visitor)
        result.nodes.extend(visitor.nodes)
        result.files_parsed += 1
    counts: dict[str, int] = {}
    for node in result.nodes:
        counts[node.id] = counts.get(node.id, 0) + 1
    callable_ids = {
        n.id
        for n in result.nodes
        if n.type in ("function", "method", "class") and counts[n.id] == 1
    }
    # Repeated definitions share a display identity but are not resolved as calls.
    result.nodes = list({n.id: n for n in result.nodes}.values())
    module_ids = {n.id for n in result.nodes if n.type == "module"}
    for visitor in visitors:
        result.edges.extend(
            e for e in visitor.edges if e.type != "imports" or e.target in module_ids
        )
        for caller, name, location in visitor.calls:
            target = visitor.resolve(caller, name)
            if target in callable_ids:
                result.edges.append(Edge(caller, target, "calls", [location]))
            else:
                result.unresolved_calls += 1
                if len(result.diagnostics) < 1000:
                    result.diagnostics.append(
                        {
                            "kind": "unresolved_call",
                            "node_id": caller,
                            "target": name,
                            **location,
                            "reason": "Target is dynamic, external, shadowed, or not uniquely bound.",
                        }
                    )
    grouped = {}
    for edge in result.edges:
        key = (edge.source, edge.target, edge.type)
        if key not in grouped:
            grouped[key] = edge
        else:
            grouped[key].evidence.extend(
                site for site in edge.evidence if site not in grouped[key].evidence
            )
    result.edges = list(grouped.values())
    return result
