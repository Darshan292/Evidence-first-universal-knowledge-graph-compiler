"""Deterministic Python backend built on the standard library `ast`.

`ast` is the *normative* Python grammar -- what CPython itself parses -- so its
structural output is definitional rather than approximate. The tradeoff, measured
and accepted: it raises on malformed input and recovers nothing, so a syntax
error yields an explicit FAILED artifact with a diagnostic, never an empty graph
that looks like a file with no symbols.

Resolution policy (never guesses):
  DETERMINISTIC  target bound in this module's own scope
  HEURISTIC      target is an imported name -- the binding is known, the
                 definition is in another artifact we have not analysed
  UNRESOLVED     everything else: dynamic dispatch, attribute calls on values,
                 builtins, star-imports
"""
from __future__ import annotations

import ast

from kgc.analysis.interface import CodeAnalysis, RawReference, RawSymbol
from kgc.ir import Diagnostic, Locator, LocatorKind, ParseStatus, Resolution

BACKEND_ID = "python_ast"
BACKEND_VERSION = "1.0.0"


def _line_offsets(data: bytes) -> list[int]:
    offs, pos = [0], 0
    for line in data.splitlines(keepends=True):
        pos += len(line)
        offs.append(pos)
    return offs


def _byte_locator(offs: list[int], node: ast.AST, data: bytes) -> Locator:
    """Exact byte range. `col_offset` is a UTF-8 byte offset within its line."""
    ls = getattr(node, "lineno", 1)
    le = getattr(node, "end_lineno", ls) or ls
    cs = getattr(node, "col_offset", 0)
    ce = getattr(node, "end_col_offset", cs) or cs
    start = offs[ls - 1] + cs
    end = offs[le - 1] + ce
    return Locator(LocatorKind.BYTE_RANGE, {
        "byte_start": start, "byte_end": min(end, len(data)),
        "line_start": ls, "line_end": le, "col_start": cs, "col_end": ce})


def analyze(artifact_id: str, data: bytes, module_name: str = "module") -> CodeAnalysis:
    an = CodeAnalysis(backend_id=BACKEND_ID, backend_version=BACKEND_VERSION,
                      language="python", parse_status=ParseStatus.OK)
    try:
        text = data.decode("utf-8")
        tree = ast.parse(text)
    except SyntaxError as e:
        an.parse_status = ParseStatus.FAILED
        an.parse_error = f"{type(e).__name__}: {e.msg} (line {e.lineno})"
        an.diagnostics.append(Diagnostic(artifact_id, "ERROR", "SYNTAX_ERROR",
                                         an.parse_error, e.lineno))
        return an                      # explicit failure, never an empty success
    except (ValueError, RecursionError) as e:
        an.parse_status = ParseStatus.FAILED
        an.parse_error = f"{type(e).__name__}: {e}"
        an.diagnostics.append(Diagnostic(artifact_id, "ERROR", "PARSE_ERROR", an.parse_error, None))
        return an

    offs = _line_offsets(data)
    module_loc = Locator(LocatorKind.BYTE_RANGE, {
        "byte_start": 0, "byte_end": len(data), "line_start": 1,
        "line_end": max(1, len(offs) - 1), "col_start": 0, "col_end": 0})
    an.symbols.append(RawSymbol("module", module_name, module_name, None,
                                module_loc, ast.get_docstring(tree)))

    module_scope: dict[str, str] = {}   # local name -> qualified name
    imported: dict[str, str] = {}       # local name -> dotted origin

    def walk(node: ast.AST, parent_q: str, is_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{parent_q}.{child.name}"
                an.symbols.append(RawSymbol(
                    "method" if is_class else "function", child.name, q, parent_q,
                    _byte_locator(offs, child, data), ast.get_docstring(child)))
                if parent_q == module_name:
                    module_scope[child.name] = q
                walk(child, q, False)

            elif isinstance(child, ast.ClassDef):
                q = f"{parent_q}.{child.name}"
                an.symbols.append(RawSymbol("class", child.name, q, parent_q,
                                            _byte_locator(offs, child, data),
                                            ast.get_docstring(child)))
                if parent_q == module_name:
                    module_scope[child.name] = q
                for base in child.bases:
                    bname = _dotted(base)
                    if bname:
                        tq, res, why = _resolve(bname, module_scope, imported)
                        an.references.append(RawReference(
                            q, "EXTENDS", bname, tq, res,
                            _byte_locator(offs, base, data), why))
                walk(child, q, True)

            elif isinstance(child, ast.Import):
                for alias in child.names:
                    local = alias.asname or alias.name.split(".")[0]
                    imported[local] = alias.name
                    an.references.append(RawReference(
                        parent_q, "IMPORTS", alias.name, None, Resolution.HEURISTIC,
                        _byte_locator(offs, child, data),
                        "import target is in another artifact; binding known, definition not analysed"))

            elif isinstance(child, ast.ImportFrom):
                mod = child.module or "."
                for alias in child.names:
                    if alias.name == "*":
                        an.references.append(RawReference(
                            parent_q, "IMPORTS", f"{mod}.*", None, Resolution.UNRESOLVED,
                            _byte_locator(offs, child, data),
                            "star-import: bound names are not statically determinable"))
                        an.diagnostics.append(Diagnostic(
                            artifact_id, "WARNING", "STAR_IMPORT",
                            f"star-import from {mod} makes later names unresolvable", child.lineno))
                        continue
                    local = alias.asname or alias.name
                    imported[local] = f"{mod}.{alias.name}"
                    an.references.append(RawReference(
                        parent_q, "IMPORTS", f"{mod}.{alias.name}", None, Resolution.HEURISTIC,
                        _byte_locator(offs, child, data),
                        "import target is in another artifact"))

            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        q = f"{parent_q}.{t.id}"
                        kind = "constant" if t.id.isupper() else "variable"
                        an.symbols.append(RawSymbol(kind, t.id, q, parent_q,
                                                    _byte_locator(offs, child, data)))
                        if parent_q == module_name:
                            module_scope[t.id] = q
                walk(child, parent_q, is_class)

            else:
                walk(child, parent_q, is_class)

    walk(tree, module_name, False)
    an.bindings = dict(imported)
    an.module_defs = set(module_scope)

    # Calls are collected after the full scope is known: a call may precede
    # the definition it targets.
    enclosing = _build_enclosing_map(tree, module_name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if not name:
                an.references.append(RawReference(
                    enclosing.get(id(node), module_name), "CALLS", "<computed>", None,
                    Resolution.UNRESOLVED, _byte_locator(offs, node, data),
                    "call target is a computed expression"))
                continue
            tq, res, why = _resolve(name, module_scope, imported)
            an.references.append(RawReference(
                enclosing.get(id(node), module_name), "CALLS", name, tq, res,
                _byte_locator(offs, node, data), why))
            if res is Resolution.UNRESOLVED:
                an.diagnostics.append(Diagnostic(
                    artifact_id, "INFO", "UNRESOLVED_REFERENCE",
                    f"call target {name!r} not statically resolvable", node.lineno))
    return an


def _resolve(name: str, scope: dict, imported: dict):
    head = name.split(".")[0]
    if name in scope:
        return scope[name], Resolution.DETERMINISTIC, "bound in module scope"
    if head in scope and "." in name:
        return None, Resolution.UNRESOLVED, "attribute access on a module-scope value"
    if head in imported:
        return None, Resolution.HEURISTIC, f"imported name resolves to {imported[head]!r} in another artifact"
    return None, Resolution.UNRESOLVED, "not bound in any statically known scope"


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _build_enclosing_map(tree: ast.AST, module_name: str) -> dict[int, str]:
    out: dict[int, str] = {}

    def rec(node, q):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                cq = f"{q}.{child.name}"
                out[id(child)] = cq
                rec(child, cq)
            else:
                out[id(child)] = q
                rec(child, q)
    rec(tree, module_name)
    return out
