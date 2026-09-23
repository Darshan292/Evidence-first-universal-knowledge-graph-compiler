# GATE 2.5 REPORT — Predicate Conflict Semantics + Exact Scope

**Date:** 2026-09-23 · **Verdict: CONDITIONAL PASS**

Both findings were real. I reproduced each through the full `decide()` path
before changing anything, and **Finding B turned out worse than reported**.
Both are corrected, covered by 22 new regression tests (84 total), audited, and
backed by executable invariants that hold on both real corpora.

The single condition is a limitation I could not close inside this gate's
scope, stated in §9.

---

## 1. Both findings reproduced first

**Finding A** — `what does Service.run call` on a fixture whose `run()` calls two
functions returned:

```
EXPOSE_CONFLICTED
conflict: CONTRADICTS  values: ['src.utils.helper_a', 'src.other.helper_b']
```

Two correct call targets reported as a dispute.

**Finding B** — `scope='utils.py'` matched **all three** of `src/utils.py`,
`tests/utils.py`, `vendor/utils.py`.

### Finding B was worse than described

The ambiguity guard read:

```python
if len(distinct) > 1 and not c.source_scope:      # BEFORE
```

**Supplying a scope disabled the guard.** So a bare basename matched three
artifacts, skipped the ambiguity check entirely, and one was selected by row
order — the exact "silently choose one based on ordering" failure the gate
prohibits. Suffix matching alone would merely have widened the candidate set;
combined with the guard bypass it *decided identity by ordering*.

A third defect surfaced while fixing it: `FILE_SCOPE` matched only a bare
basename, so an exact scope was **inexpressible** — `src/utils.py` was read as
`utils.py`.

## 2. Two corrections to your specification

**`EXTENDS` was absent from your cardinality list, and it is the dangerous one.**
Python permits multiple inheritance, so `class C(A, B)` emits two EXTENDS facts.
Had it defaulted to functional, multiple inheritance would be reported as a
contradiction — reproducing the very defect this gate corrects. It is
`MULTI_VALUED`. (`DEFINES` and `HAS_PURPOSE` also needed rulings: MULTI_VALUED
and FUNCTIONAL respectively.)

**Your §5 fixture cannot be exercised end-to-end as written.** `HAS_DEFAULT` is
declared but never emitted — **7 of 11 predicates are vocabulary-only**
(`DEFINES`, `READS`, `WRITES`, `HAS_DEFAULT`, `HAS_VALUE`, `HAS_TYPE`,
`HAS_PURPOSE`). Functional conflict is therefore tested by inserting claims
directly, and the tests say so. The deeper issue — the vocabulary promising
capability the compiler lacks — is now recorded in the spec itself via
`emitted_by_compiler`, and is §9's condition.

## 3. What was changed

**Predicate spec** (`kgc/predicates.py`) gained `cardinality`
(`FUNCTIONAL` | `MULTI_VALUED`), `may_contradict` and `emitted_by_compiler`.
`_conflict()` consults the spec and **contains no predicate names of its own** —
asserted by a test that greps its body. Unknown predicates default to
`MULTI_VALUED`: inventing a contradiction is worse than missing one.

**Artifact identity** (`kgc/artifact_identity.py`) is the one authoritative
module. Normalization happens once, at ingestion. `resolve_scope` returns
`EXACT` / `AMBIGUOUS` / `UNKNOWN`; ambiguous abstains. Comparison is exact and
case-sensitive. The ambiguity guard is now unconditional.

## 4. The finding the metrics could not see

Re-running corpus3 pre- and post-fix:

| | Pre-fix | Post-fix | Changed |
|---|---|---|---|
| **Decisions shifted `EXPOSE_CONFLICTED` → `EXPOSE`** | — | — | **30 of 132 (23%)** |
| TEST false support | 0.000 | 0.000 | no |
| TEST false abstention | 0.143 | 0.143 | no |
| TEST claim Recall@10 | 0.810 | 0.810 | no |
| TEST evidence Recall@10 | 0.524 | 0.524 | no |

**Twenty-three percent of queries were mis-reported as conflicts, and every
headline number is identical** — because the gold accepted `EXPOSE` or
`EXPOSE_CONFLICTED` for answerable queries.

> The evaluation was structurally incapable of seeing this defect. It was found
> by reading the code. Any metric that accepts a set of outcomes cannot
> distinguish between them, and a suite of 62 passing tests said nothing.

## 5. Historical results: every change explained

| Evaluation | Metric | Pre | Post | Explanation |
|---|---|---|---|---|
| corpus3 gate225 | all headline metrics | — | unchanged | the fix altered 30 decisions the gold scored as equivalent (§4) |
| corpus2 c2-generalization | precision | 1.000 | **1.000** | verifier tightened from suffix to exact identity; **38/38 survives the stricter check** |
| corpus3 independent verification | definition / EXTENDS / evidence | 1.000 | 1.000 | unaffected |
| corpus2 claim_first | false support | 0.000 | 0.000 | unaffected |
| corpus2 claim_first | false abstention | 0.500 | **0.600** | **not caused by this gate** — see below |
| corpus2 claim_first | doc Recall@10 | 0.450 | **0.300** | same cause |

The corpus2 availability drop is the **Gate 2.25 stricter query IR**, not Gate
2.5: parse rate on those hand-written queries fell 0.70 → 0.50 because the IR
now returns `PARTIAL`/`UNPARSEABLE` instead of silently answering a different
question. It is the safety/availability trade, and it is the correct direction.

### A process failure found by re-running

`claim_first_eval.py` had been **broken since Gate 2.25**: I rewrote
`claimfirst.py`, changed `decide()` from a 4-tuple to a `Decision` object,
removed `retrieve_claims`, and never re-ran the preserved experiment. It raised
`ImportError` at the first re-run in this gate. Repaired and re-run.

**Preserving an experiment is not the same as keeping it runnable.** Every gate
from here should execute the preserved suite, not merely retain the files.

## 6. Invariants (`experiments/semantic_invariants.py`)

| ID | Invariant | corpus2 | corpus3 |
|---|---|---|---|
| I-1 | a MULTI_VALUED predicate cannot become CONTRADICTS from differing valid objects | **holds** | **holds** |
| I-2 | a scoped decision cannot expose evidence from another canonical artifact | **holds** | **holds** |
| I-3 | no semantic identity depends on candidate ordering (and decisions are repeatable) | **holds** | **holds** |

## 7. Audits

`IDENTITY_SCOPE_AUDIT.md` classifies all twelve suffix/prefix/basename/case
comparisons. **Three were semantic identity and were made exact**; the rest are
filesystem filtering, text processing, retrieval scoring or gold construction
and were deliberately left alone. Replacing all of them would have broken
correct code.

`CLAIM_SEMANTICS.md` now carries the full predicate audit — subject, object,
kind, determinism, cardinality, conflict semantics, establishment, and whether
the compiler emits it — as the **only** predicate table in the documentation.
The earlier table, which omitted cardinality, was removed rather than kept in
parallel.

## 8. Simplification pass

Removed: the superseded predicate table, the dead `is_canonical`, and a second
path normalizer in `_module_name` (now calls `canonical_path`). Verified: no
duplicate predicate metadata outside the spec, one authoritative location each
for predicate semantics and artifact identity, no query-specific exceptions, no
new wrappers.

## 9. Answers

1. **Multi-valued distinguished from functional?** **Yes** — declared on every
   predicate in the spec, with `EXTENDS`, `DEFINES`, `READS`, `WRITES`,
   `CONTAINS`, `CALLS`, `IMPORTS` multi-valued and the four `HAS_*` functional.
2. **Can valid multi-valued relationships become conflicts?** **No.**
   `_conflict()` skips any predicate whose spec says it cannot contradict;
   invariant I-1 confirms it on both corpora; three tests cover CALLS, IMPORTS
   and multiple inheritance through `decide()`.
3. **Are functional conflicts detected semantically?** **Yes**, on normalized
   values — but **via injected claims**, because no functional predicate is
   emitted (§2).
4. **Are equivalent normalized values consistent?** **Yes** — `30 seconds`/`30 s`
   and `0.5 minutes`/`30 s` compare `SAME`.
5. **Are unsupported values left unresolved?** **Yes** — `30 frobnitzes` vs
   `30 seconds`, and prose vs prose, return `UNRESOLVED` rather than a guess.
6. **Is source scope exact?** **Yes** — exact canonical path, case-sensitive,
   applied to both entity resolution and evidence selection.
7. **Can same-basename artifacts be confused?** **No.** A qualified scope selects
   exactly one; a bare basename matching several returns `ABSTAIN_AMBIGUOUS`;
   an unknown basename returns `ABSTAIN`.
8. **Are predicate semantics single-sourced?** **Yes** — `kgc/predicates.py`;
   a test fails if `_conflict()` names any predicate or redefines cardinality.
9. **Are identity/path semantics single-sourced?** **Yes** —
   `kgc/artifact_identity.py`; normalization occurs once, at ingestion.
10. **Did any historical evaluation change?** **Yes, three** — all explained in
    §5: 30 corpus3 decisions corrected with headline metrics unchanged; corpus2
    availability fell due to the Gate 2.25 IR, not this gate; and the c2
    verifier was tightened with its result unchanged at 38/38.
11. **What remains unproven?** That functional conflict behaves correctly on
    *compiled* claims (untestable until a functional predicate is emitted); that
    the fixes hold on a language with different scoping; and everything deferred
    to J-1 … J-5.

## 10. Verdict: CONDITIONAL PASS

Both defects are corrected, verified end-to-end, audited, invariant-checked and
simplified. The only reason this is not a PASS:

> **Functional conflict cannot be exercised through ingestion.** Your §5 fixture
> asks for `Service HAS_DEFAULT 30s / 60s` tested through `decide()`. No adapter
> emits `HAS_DEFAULT`, so those tests inject claims. The conflict *logic* is
> proven; the *path from source to functional conflict* is not, because it does
> not exist yet.

### Condition

| # | Condition |
|---|---|
| **K-1** | Emit at least one FUNCTIONAL predicate from the compiler — `HAS_VALUE` over class-level literal assignments is the smallest — so functional conflict is testable end to end. This is the first slice of J-3 and I did **not** implement it here, because adding a claim type is outside a narrow correction gate. |

## 11. Reproducing

```bash
python3 -m unittest discover -s tests -t .          # 84 tests
python3 experiments/semantic_invariants.py          # I-1, I-2, I-3 on both corpora
python3 experiments/gate225_eval.py                 # corpus3
python3 experiments/claim_first_eval.py             # corpus2 (repaired)
python3 experiments/c2_generalization.py            # stricter verifier
python3 experiments/independent_claim_verification.py
```

Preserved with `_PREFIX` copies for comparison: `gate225_results`,
`claim_first_results`, `independent_verification_results`,
`c2_generalization_results`. No historical result was rewritten.
