"""Language-independent code analysis boundary (ADR-0005).

A backend is a function, not a class hierarchy:

    analyze(artifact_id, source_bytes) -> CodeAnalysis

The seam exists so a parser's worldview never reaches the canonical IR. It is
why adding Tree-sitter, SCIP or a compiler backend later is a contained change
rather than a rewrite -- and why `Resolution` is mandatory on every reference.

Nothing here assumes Python.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from kgc.ir import Diagnostic, Locator, ParseStatus, Resolution


@dataclass
class RawSymbol:
    kind: str                 # module|class|function|method|variable|constant
    name: str
    qualified_name: str
    parent_qname: str | None
    locator: Locator
    docstring: str | None = None


@dataclass
class RawReference:
    from_qname: str | None
    predicate: str            # CALLS | IMPORTS | EXTENDS | READS ...
    to_name: str              # surface text, always present
    to_qname: str | None      # None when UNRESOLVED
    resolution: Resolution    # MANDATORY -- UNRESOLVED is a recorded fact
    locator: Locator
    reason: str = ""


@dataclass
class CodeAnalysis:
    """What a backend observed. Converted to IR by mapper.py, never directly."""
    backend_id: str
    backend_version: str
    language: str
    parse_status: ParseStatus
    symbols: list[RawSymbol] = field(default_factory=list)
    references: list[RawReference] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    parse_error: str | None = None
    # Import bindings: local name -> dotted origin. Populated by the backend so a
    # corpus-level resolver can bind names without re-implementing the grammar.
    bindings: dict = field(default_factory=dict)
    # Names defined at module level in THIS module. A local definition shadows an
    # imported name of the same name -- resolving without this produces a wrong
    # deterministic edge, which D-H vetoes.
    module_defs: set = field(default_factory=set)
