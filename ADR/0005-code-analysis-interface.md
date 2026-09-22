# ADR-0005: Code analysis interface

- **Status:** Accepted (Phase 0, adversarial review)
- **Date:** 2026-09-22
- **Amends:** ADR-0002 (canonical IR)

## Context

Phase 0 chose stdlib `ast` for Milestone 1 and deferred tree-sitter. The choice
was defensible, but Phase 0 never wrote down a code-analysis interface — so the
only concrete code model in the design was whatever `ast` returns. That is how a
Python-shaped representation becomes canonical by accident, and it is the defect
the adversarial review identified.

Defining an interface is not premature generalisation. *Implementing six
backends* would be. The interface is derived from what multiple analysis
technologies can express; only one backend is built.

## Decision

**A language-independent Code Analysis Interface is defined now. Exactly one
backend (Python) is implemented in M1.**

A backend is a function, not a class hierarchy:

```
analyze(artifact) -> CodeAnalysis
```

`CodeAnalysis` contains only source-independent concepts:

```
Repository, File, Module, Symbol, Function, Class, Variable, Type,
Reference, Call, Import, Inheritance, Configuration, API, Database, Table
```

### Rule 1 — Every emitted relation carries a resolution class

```
DETERMINISTIC  the backend proved it (grammar, binding, or compiler evidence)
HEURISTIC      the backend inferred it by a stated rule that can be wrong
UNRESOLVED     the backend saw the reference and could not resolve the target
```

`UNRESOLVED` is **mandatory, not optional**. A backend that encounters a call it
cannot resolve must emit an `UNRESOLVED` reference. Omitting it is a defect: a
missing edge is indistinguishable from "no such call exists", which silently
misinforms every downstream query. A recorded non-answer is a fact; an absence is
a lie by omission.

### Rule 2 — Absence is permitted; fabrication is not

Not every language supplies every concept. The interface allows a backend to
emit nothing for a concept, and the coverage report states what was not attempted.

| Concept | Python (M1) | TS/JS | Java | Go | Rust | C/C++ |
|---|---|---|---|---|---|---|
| Module / File / Symbol | ✅ D | ✅ | ✅ | ✅ | ✅ | ✅ |
| Function / Class / Variable | ✅ D | ✅ | ✅ | ✅ | ✅ | ✅ |
| Import | ✅ D | ✅ D | ✅ D | ✅ D | ✅ D | ⚠ preprocessor |
| Call (intra-module) | ✅ D | ✅ D | ✅ D | ✅ D | ✅ D | ⚠ macros |
| Call (cross-module) | ⚠ H | ⚠ H | ✅ D (with compiler) | ✅ D | ✅ D | ⚠ H |
| Type | ❌ (dynamic) | ⚠ with `tsc` | ✅ | ✅ | ✅ | ⚠ |
| Inheritance | ✅ D syntactic | ✅ | ✅ D | n/a (interfaces) | ⚠ traits | ✅ |
| Configuration | ⚠ H (literal assign) | ⚠ H | ⚠ H | ⚠ H | ⚠ H | ⚠ H |
| API / Database / Table | ⚠ H (framework rules) | ⚠ H | ⚠ H | ⚠ H | ⚠ H | ⚠ H |

`D` = DETERMINISTIC achievable, `H` = HEURISTIC only, `❌` = not derivable.
**`API`, `Database` and `Table` are HEURISTIC in every language** — they come
from framework-specific rules (ORM models, route decorators, SQL string
literals), never from the grammar. They are therefore always candidate claims
requiring evidence, never structural facts.

### Rule 3 — The backend never produces IR rows directly

A backend returns `CodeAnalysis`; a single mapper converts it to IR. This is the
seam that keeps a parser's worldview out of the canonical model, and it is what
makes backend #2 a contained change.

## Backend selection

| Backend | Status | Adoption trigger |
|---|---|---|
| Python `ast` + `symtable` | **M1** | — |
| Tree-sitter | deferred | language #2 arrives, **or** parse-failure rate > 2% on a real corpus (stdlib `ast` recovers zero symbols from an unparseable file — measured) |
| SCIP (`scip-python`/Pyright) | deferred | measured cross-module call recall < 0.85 with the `ast` backend |
| Compiler/LSP (`tsc`, `javac`, `gopls`) | deferred | a language whose useful resolution genuinely requires a build |

## Abstraction ledger

- **Problem now:** prevent a Python-specific model from becoming canonical, and
  make resolution quality explicit rather than implied.
- **Considered:** emitting IR directly from `ast` (Phase 0's implicit design).
- **Why insufficient:** it makes every downstream consumer depend on Python
  semantics, and provides no place to express `HEURISTIC` vs `UNRESOLVED`.
- **Introduced:** one dataclass set and one mapper function.
- **Removed:** per-language branching in every consumer; the ambiguity about what
  an absent edge means.
- **Without it:** backend #2 becomes a rewrite, and "we don't know" is
  indistinguishable from "it isn't there".

## Consequences

**Good.** Resolution quality is queryable (`WHERE resolution='HEURISTIC'`).
Adding a language is one backend plus capability-matrix rows. Precision targets
become enforceable per resolution class.

**Bad.** One indirection in M1 that a Python-only tool would not need. Accepted:
the alternative was an accidental canonical model.

**Accepted.** Cross-module Python resolution stays `HEURISTIC`. Python is
dynamically typed; claiming otherwise would be the fabrication this project exists
to prevent.

---

## Implementation findings (Gate 1, 2026-09-22)

**Implemented with exactly one backend**, as specified. The `CodeAnalysis` ->
IR mapper keeps `ast` semantics out of the canonical model.

**[MEASURED] Resolution rates on real code** (the compiler ingesting itself,
599 references):

| Resolution | Share | Dominant cause |
|---|---|---|
| `DETERMINISTIC` | 11.2% | module-scope calls |
| `HEURISTIC` | 32.7% | imports — statement observed, target in another artifact |
| `UNRESOLVED` | **56.1%** | 293 unbound names (builtins, methods on locals), 41 computed call targets, 2 attribute accesses |

Rule 1 held under pressure: every unresolvable reference produced an
`UNRESOLVED` row with its surface name and a reason. None was dropped, and an
audit asserts no `UNRESOLVED` row carries a resolved target.

**Deleted:** `CodeAnalysis.capability_report()`. The CLI derives the same
breakdown from persisted rows, which is the real truth rather than an in-memory
summary of one run.

**Open:** cross-module resolution is not implemented, so **100% of `IMPORTS` are
`HEURISTIC`**. Gate 1 condition C-2 requires deciding whether to build an import
resolver or accept this permanently, before retrieval is measured — otherwise
X-1 would be measuring a graph whose cross-file edges are all unresolved.
