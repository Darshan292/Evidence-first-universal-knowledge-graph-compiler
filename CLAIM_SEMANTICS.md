# CLAIM SEMANTICS

**Status:** Implemented in `kgc/predicates.py`. **Date:** 2026-09-23

## 1. Why a canonical vocabulary

The system was becoming dependent on natural-language property names: a query
saying "separator" had to find an attribute called `sep`, and nothing recorded
what either meant. A predicate must have **one** semantic meaning.

Only predicates the measured corpora require are defined. The vocabulary is not
padded for features that do not exist.

## 2. The vocabulary — full predicate audit

Every field of the specification, in one place. This is the ONLY predicate table in the documentation; an earlier table omitting cardinality was removed rather than kept in parallel. `_conflict()` reads
`cardinality` from here; it contains no predicate names of its own.

| Predicate | Subject | Object | Kind | Deterministic | Cardinality | Conflict semantics | Establishment allowed | Emitted today |
|---|---|---|---|---|---|---|---|---|
| `CONTAINS` | symbol | symbol | structural | yes | **MULTI_VALUED** | never contradicts | DERIVED, CONFIRMED | yes |
| `DEFINES` | artifact | symbol | structural | yes | **MULTI_VALUED** | never contradicts | DERIVED, CONFIRMED | **no** |
| `CALLS` | symbol | symbol|literal | structural | yes | **MULTI_VALUED** | never contradicts | DERIVED, CONFIRMED | yes |
| `IMPORTS` | symbol | symbol|literal | structural | yes | **MULTI_VALUED** | never contradicts | DERIVED, CONFIRMED | yes |
| `EXTENDS` | symbol | symbol|literal | structural | yes | **MULTI_VALUED** | never contradicts | DERIVED, CONFIRMED | yes |
| `READS` | symbol | symbol | structural | yes | **MULTI_VALUED** | never contradicts | DERIVED, CONFIRMED | **no** |
| `WRITES` | symbol | symbol | structural | yes | **MULTI_VALUED** | never contradicts | DERIVED, CONFIRMED | **no** |
| `HAS_DEFAULT` | symbol | literal | structural | yes | **FUNCTIONAL** | can contradict | DERIVED, CONFIRMED | **no** |
| `HAS_VALUE` | symbol | literal | structural | yes | **FUNCTIONAL** | can contradict | DERIVED, CONFIRMED | yes, direct class-body literals only (K-1) |
| `HAS_TYPE` | symbol | symbol|literal | structural | no | **FUNCTIONAL** | can contradict | DERIVED, CONFIRMED | **no** |
| `HAS_PURPOSE` | symbol | literal | semantic | no | **FUNCTIONAL** | can contradict | DERIVED, CONFIRMED, PROPOSED, DISPUTED | **no** |

### Cardinality rulings

**`EXTENDS` is `MULTI_VALUED`.** Python permits multiple inheritance, so
`class C(A, B)` emits two EXTENDS facts. The Gate 2.5 instruction's example list
did not mention EXTENDS; had it defaulted to functional, multiple inheritance
would have been reported as a contradiction — reproducing the very defect the
gate corrects.

**`HAS_PURPOSE` is `FUNCTIONAL`** despite being the one semantic predicate: a
symbol has one documented purpose, so two sources asserting different purposes
is a genuine dispute.

**`DEFINES`, `READS`, `WRITES` are `MULTI_VALUED`** — an artifact defines many
symbols; a function reads and writes many values.

**Unknown predicates default to `MULTI_VALUED`.** Inventing a contradiction is
worse than missing one: a false CONTRADICTS presents two correct facts as a
dispute.

### Six of eleven predicates are not emitted

`DEFINES`, `READS`, `WRITES`, `HAS_DEFAULT`, `HAS_TYPE` and `HAS_PURPOSE` are
declared but **no adapter produces them**. The vocabulary still promises
capability the compiler does not have. This is recorded in the spec itself
(`emitted_by_compiler`) so the gap is visible rather than implied, and it is the
substance of condition J-3.

`HAS_VALUE` left that list in K-1, but only for one shape: an assignment that is
a direct child of a class body, with exactly one plain-name target and a bare
literal on the right. Nothing is evaluated — `TIMEOUT = 10 + 20` is refused with
a diagnostic, not folded to 30 — and module-level constants, annotated
assignments and anything inside a function are all out of scope. Measured
coverage and the exact refusal counts are in K1_REPORT.md §3.

Consequence for testing: functional conflict is now demonstrated end to end on
compiled `HAS_VALUE` claims. The remaining functional predicates are still
vocabulary-only, so tests that need them insert claims directly and say so.

## 3. Property-word mapping

A tiny, explicit table maps natural-language property words to predicates:

```
default, defaults  → HAS_DEFAULT
value              → HAS_VALUE
type               → HAS_TYPE
purpose, rationale → HAS_PURPOSE
```

An **unmapped** property word is reported unmapped. It is never guessed, and it
never silently becomes a different predicate.

## 4. Independent verification at scale

A deterministically generated graph is not ground truth merely because it was
generated deterministically. Sampled from corpus3 and re-derived by a **separate
stdlib AST procedure**:

| Claim type | Population | Sample | Confirmed | Precision |
|---|---|---|---|---|
| definition (`CONTAINS` → function/method/class) | 2,197 | 400 | 400 | **1.000** |
| `EXTENDS` | 154 | 150 | 150 | **1.000** |
| evidence spans re-read byte-for-byte | — | 400 | 400 | **1.000** |

Claims lacking verifiable evidence, out of 14,291: **0**.

> The first run of this check reported `EXTENDS` precision 0.84. The
> disagreements were all dotted bases (`class X(r.BaseConverter)`), which my
> *checker* recorded only as `ast.Name`. The checker was wrong, not the graph.
> Recorded because a verifier's own bugs are the easiest way to publish a false
> defect — or to miss a real one.
