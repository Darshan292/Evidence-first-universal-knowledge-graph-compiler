# CLAIM SEMANTICS

**Status:** Implemented in `kgc/predicates.py`. **Date:** 2026-09-23

## 1. Why a canonical vocabulary

The system was becoming dependent on natural-language property names: a query
saying "separator" had to find an attribute called `sep`, and nothing recorded
what either meant. A predicate must have **one** semantic meaning.

Only predicates the measured corpora require are defined. The vocabulary is not
padded for features that do not exist.

## 2. The vocabulary

| Predicate | Meaning | Subject | Object | Structural | Deterministic today | Establishment allowed |
|---|---|---|---|---|---|---|
| `CONTAINS` | subject lexically encloses object | symbol | symbol | yes | yes | DERIVED, CONFIRMED |
| `DEFINES` | artifact introduces symbol | artifact | symbol | yes | yes | DERIVED, CONFIRMED |
| `CALLS` | subject has a call site targeting object | symbol | symbol\|literal | yes | yes | DERIVED, CONFIRMED |
| `IMPORTS` | subject module binds a name from object | symbol | symbol\|literal | yes | yes | DERIVED, CONFIRMED |
| `EXTENDS` | subject class derives from object class | symbol | symbol\|literal | yes | yes | DERIVED, CONFIRMED |
| `READS` | subject reads object's value | symbol | symbol | yes | yes | DERIVED, CONFIRMED |
| `WRITES` | subject assigns object's value | symbol | symbol | yes | yes | DERIVED, CONFIRMED |
| `HAS_DEFAULT` | subject's default value is object | symbol | literal | yes | yes | DERIVED, CONFIRMED |
| `HAS_VALUE` | subject's literal value is object | symbol | literal | yes | yes | DERIVED, CONFIRMED |
| `HAS_TYPE` | subject's declared type is object | symbol | symbol\|literal | yes | **no** | DERIVED, CONFIRMED |
| `HAS_PURPOSE` | subject's documented purpose is object | symbol | literal | **no** | no | DERIVED, CONFIRMED, PROPOSED, DISPUTED |

`HAS_PURPOSE` is the only non-structural predicate, and the only one a model may
ever propose. Everything else is parser-derivable and a model-only assertion of
it is rejected by the claim builder.

`HAS_TYPE` is declared but **not deterministically derivable today** — Python is
dynamically typed. It is listed so the boundary is explicit, not to imply
coverage.

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
