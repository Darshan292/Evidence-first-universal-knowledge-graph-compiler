# CLAIM TRUST BOUNDARY

**Status:** Enforced in code and covered by regression tests. **Date:** 2026-09-23

## 1. The boundary

```
candidate discovery        ← retrieval lives here, and ONLY here
        ↓
compiled claim
        ↓
claim semantics            ← canonical predicate, normalized value
        ↓
verified evidence
        ↓
SUPPORT / CONFLICT / ABSTAIN
```

The support decision depends **only** on: interpreted query constraints, the
compiled claim, its provenance, its establishment and lifecycle, its verified
evidence, and scope/version/conflict information.

It must **not** depend on: retrieval score, rank, lexical overlap, candidate
ordering, or model confidence.

### How this is enforced, not merely stated

`experiments/retrieval/claimfirst.py` does not import the retriever. A
regression test parses its **AST** (not its text — the docstring legitimately
names the signals it refuses) and fails if any of
`bm25, rank, score, lexical, confidence, r1, r2, overlap, idf` appears as a
name, attribute or import.

> Gate 2's report described this boundary. The Gate 2 *code* carried
> `establishment` on every claim object and **never consulted it**. That gap is
> what this gate closed.

## 2. The eleven invariants

A claim is eligible for **trusted support** only when all hold:

| # | Invariant | Failure code |
|---|---|---|
| 1 | Query constraints identify the intended subject | `INV-1_constraints` / `INV-1_subject` |
| 2 | Predicate/property matches exactly or by explicit canonical mapping | `INV-2_predicate` |
| 3 | Object/value constraints match where present | `INV-3_object` |
| 4 | Scope constraints match where present | `INV-4_scope` |
| 5 | Establishment is `DERIVED` or `CONFIRMED` | `INV-5_establishment` |
| 6 | Lifecycle permits presentation | `INV-6_lifecycle` |
| 7 | Evidence exists | `INV-8_evidence` |
| 8 | Verification strength is `EXACT` or `REPRODUCIBLE` | `INV-8_evidence` |
| 9 | Evidence belongs to the claim | enforced by the SQL join + Gate 1 triggers |
| 10 | No unresolved ambiguity remains | `INV-10_ambiguity` |
| 11 | No unresolved contradiction for the presented value | → `EXPOSE_CONFLICTED` |

Every abstention names the invariant it failed. "Score below threshold" is not
an explanation; "no compiled claim provides property `'encryption_key'` for
`Request`" is.

**Confidence satisfies none of these.** `confidence` is read by no code path in
the system. A test sets it to 0.97 on an untrusted claim and asserts the claim
still cannot expose.

## 3. Establishment

| Level | May support `EXPOSE` | May appear in `EXPOSE_CONFLICTED` | Stored |
|---|---|---|---|
| `DERIVED` | **yes** | yes | yes |
| `CONFIRMED` | **yes** | yes | yes |
| `PROPOSED` | **no** | only with explicit contradiction structure | yes |
| `DISPUTED` | **no** | yes | yes |

**Untrusted claims are never deleted.** The database is a historical knowledge
store; the *support layer* decides what may be presented as verified. Tests
assert that `PROPOSED` and `SUPERSEDED` claims remain queryable after being
refused as answers.

The rule is single-sourced: `kgc/predicates.py` declares each predicate's
`allowed_establishment`, and the gate consults it. A structural predicate cannot
be trusted at `PROPOSED` because its own definition forbids it.

## 4. Decision space

| Outcome | Meaning |
|---|---|
| `EXPOSE` | every invariant satisfied |
| `EXPOSE_CONFLICTED` | satisfied, but sources assert semantically incompatible values |
| `ABSTAIN` | an invariant failed; the failing one is named |
| `ABSTAIN_AMBIGUOUS` | the subject resolves to several entities and nothing disambiguates |

`ABSTAIN_AMBIGUOUS` is separate on purpose: *"I do not know which entity you
mean"* is different from *"that fact does not exist"*, and conflating them hides
a fixable problem behind an unfixable one.

## 5. Measured

On 224 artifacts / 14,291 claims (corpus3, werkzeug):

| | CALIBRATION | VALIDATION | TEST |
|---|---|---|---|
| False support | **0.000** | **0.000** | **0.000** |
| Untrusted exposure | 0 | 0 | 0 |
| Unsupported exposure | 0 | 0 | 0 |
| Ambiguity abstention | 1.000 | 1.000 | 1.000 |
