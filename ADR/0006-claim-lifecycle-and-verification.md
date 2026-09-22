# ADR-0006: Claim lifecycle, establishment and evidence verification

- **Status:** Accepted (Phase 0, adversarial review)
- **Date:** 2026-09-22
- **Amends:** DATA_MODEL.md §6, PROJECT_SPEC.md §6
- **Replaces:** Phase 0's single `status` column and its structural-edge blacklist

## Context

The adversarial review found three coupled defects:

1. Phase 0's rule "models may not create structural edge kinds" was a
   **blacklist**, and blacklists fail at their edges. It could not answer whether
   a model may assert `CONFIGURED_BY`, or a predicate a future backend might
   resolve.
2. A single `status` column conflated *pipeline position*, *who established the
   fact*, *how checkable the evidence is*, and *standing against other claims*.
   This produced a real bug: `CONTRADICTED` as a `status` value meant a
   contradicted claim left the `ACTIVE` set and **disappeared from queries** —
   the exact opposite of the project's stated commitment.
3. Byte-substring verification does not generalise beyond text, yet Phase 0 had
   one boolean `verified` flag for all modalities. Applied to an ASR transcript
   it would assert verification the system cannot perform.

## Decision

### Part 1 — Four orthogonal axes, never collapsed

| Axis | On | Values |
|---|---|---|
| `lifecycle` | claim | `CANDIDATE`, `VALIDATING`, `VERIFIED`, `ACTIVE`, `REJECTED` |
| `establishment` | claim | `DERIVED`, `CONFIRMED`, `PROPOSED`, `DISPUTED` |
| `verification_strength` | evidence | `EXACT`, `REPRODUCIBLE`, `STRUCTURAL` |
| `epistemic_state` | claim_relation | `CONTRADICTS`, `SUPERSEDES`, `SUPPORTS`, `DERIVED_FROM` |

```
CANDIDATE → VALIDATING → VERIFIED → ACTIVE        (any failure → REJECTED)
```

`CONTRADICTED` and `SUPERSEDED` are **not lifecycle states**. A contradicted
claim remains `ACTIVE` and queryable; its standing is expressed by a relation to
the claim contradicting it. This is the correction of the disappearing-claim bug.

`confidence` is a nullable REAL meaning only "the producer's self-reported
score". It is `NULL` for `DERIVED` claims — a parser result is not a probability.
**It gates nothing.** No code path reads `confidence` to decide trust.
`confidence = 0.92` never implies `verified`.

### Part 2 — Establishment replaces the blacklist

> **A claim may stand as a structural fact if and only if an independent
> deterministic analysis establishes it. A model may propose anything; a model's
> proposal alone never establishes a structural fact.**

| `establishment` | Produced by |
|---|---|
| `DERIVED` | a deterministic analyser; reproducible |
| `CONFIRMED` | proposed by a model **and** independently re-derived by an analyser |
| `PROPOSED` | model-produced, evidence-verified, not independently derivable |
| `DISPUTED` | model proposal contradicts a deterministic result |

Predicates are partitioned:

- **Structural** — `CALLS`, `IMPORTS`, `DEFINES`, `CONTAINS`, `EXTENDS`,
  `IMPLEMENTS`, `READS`, `WRITES`. Permitted only on `DERIVED`/`CONFIRMED`.
- **Semantic** — `SEMANTICALLY_RELATED_TO`, `DESCRIBES`, `MOTIVATES`,
  `RATIONALE_FOR`, `MENTIONS`. Permitted on `PROPOSED`.

A model emitting a structural predicate is **not discarded**. The claim is stored
with the predicate rewritten to its semantic counterpart and
`establishment = PROPOSED`, retaining the evidence. If an analyser later derives
the structural edge, a separate `DERIVED` claim is created and the `PROPOSED`
claim links to it via `SUPPORTS`. If an analyser derives that no such edge
exists, the proposal becomes `DISPUTED`.

Nothing is promoted in place. Model/analyser agreement becomes a queryable
signal instead of a discarded one — strictly more information than Phase 0's
blacklist, which threw the proposal away.

### Part 3 — Verification strength is a property of the modality

| Strength | Meaning | Applies to |
|---|---|---|
| `EXACT` | content re-derived from the artifact and compared byte-for-byte | code, PDF text, DOCX, CSV, JSON, XML |
| `REPRODUCIBLE` | re-derivable only by re-running a pinned deterministic engine (engine + version + settings recorded) | OCR, ASR |
| `STRUCTURAL` | locator is valid and in-bounds; content cannot be independently re-derived | image regions, audio spans, video frames, scanned PDF regions |

**Rule:** a claim whose evidence is only `STRUCTURAL` may never reach
`establishment = CONFIRMED`, and is marked distinctly in the UI. An ASR line is
evidence that *something was said at 04:12* — not proof of *what*.

### Part 4 — Write ordering (crash-safety correction)

Phase 0's trigger fired `AFTER INSERT ON claim`, but evidence rows may not exist
at that instant within the same transaction. Correction:

1. Write evidence rows; verify each at its achievable strength.
2. Insert the claim referencing `sorted(evidence_ids)` — already required by the
   deterministic `claim_id` hash.
3. The deferred check at commit sees the evidence correctly.

Claim and evidence share **one transaction**, so a crash at any point before
commit leaves nothing partial. Orphan evidence is possible and harmless: it is
inert, and deterministic `evidence_id` means a resumed run reuses the same row.

## Consequences

**Good.** Every trust question has exactly one column that answers it. A
contradicted claim stays queryable. The system cannot claim verification it
cannot perform. Model contributions are retained rather than discarded.

**Bad.** Four columns where Phase 0 had one, and a predicate-rewrite step. Both
are justified: collapsing them caused a real bug, and the rewrite preserves
information the blacklist destroyed.

**Accepted.** `REPRODUCIBLE` re-verification is a batch operation, not an
insert-time one — re-running OCR on every insert is not viable. The engine pin is
recorded so re-verification is possible on demand.
