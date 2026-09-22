# ABSTENTION GATE — PRE-REGISTRATION

**Committed before the gate was implemented.** Git history is the evidence: this
file's commit precedes any support-gate code. **Date:** 2026-09-22

---

## 1. Fixed inputs

- **Corpus:** `eval/corpus/` (unchanged from Gate 1.5, commit `d99756e`).
- **Gold:** `eval/ABSTENTION_GOLD.json` v1.1.0 — **31 negatives** across classes
  A–H (7 of them class C, the hard-negative class) and **21 positives** across
  7 positive classes.
- **Splits:** CALIBRATION 24 / VALIDATION 15 / TEST 13, stratified so every split
  contains class C and positives. The split rule and the reason the first
  (unstratified) rule was replaced are recorded in the gold file itself.

## 2. Split discipline

| Split | Permitted use |
|---|---|
| CALIBRATION | selecting the threshold θ. **The only data θ may see.** |
| VALIDATION | comparing candidate gate designs against each other |
| TEST | **run once**, for the reported result. Never used to select anything. |

If TEST is run more than once, every run is reported, including the ones that
looked worse.

## 3. Decision space

`ABSTAIN` · `EXPOSE` · `EXPOSE_CONFLICTED` (rationale in `ABSTENTION_DESIGN.md` §1).

Scoring: a query is **correct** if the gate's decision is in the gold's
`expected_decision` list. Class G negatives accept `ABSTAIN` **or**
`EXPOSE_CONFLICTED` — exposing a contradicted value *with the conflict marked* is
honest; exposing it silently is not.

## 4. Thresholds fixed in advance

**Safety (the binding requirement).**

| Metric | Threshold |
|---|---|
| False-support rate, all negatives, TEST | **≤ 0.10** |
| False-support rate, **class C** (lexically overlapping, unsupported), TEST | **≤ 0.15** |
| Unsupported exposure (EXPOSE without a verified evidence span) | **exactly 0** |

The third is not a threshold but an invariant: it is the project's founding
guarantee, and any non-zero value is a defect, not a tuning outcome.

**Availability (the counterweight).**

| Metric | Threshold |
|---|---|
| False-abstention rate on answerable queries, TEST | **≤ 0.30** |
| Answerable acceptance rate, TEST | **≥ 0.70** |

A gate that abstains on everything is invalid, not safe. Both axes must pass.

**Retrieval must not regress.** The gate sits after ranking, so Recall@k and MRR
must be unchanged from Gate 1.5 by construction. Any change is a bug.

## 5. Evidence requirement

**A candidate may not pass the gate without a localized, verified evidence span.**
No exception, and no configuration where this is relaxed will be measured. This
is condition S1 in the design and is mandatory rather than calibrated.

## 6. Threshold selection rule — fixed now

θ is the IDF percentile at which a query term counts as "distinctive" (design
signal S2). Selection procedure, executed on **CALIBRATION only**:

1. Sweep θ over the percentiles {50, 60, 70, 75, 80, 85, 90, 95}.
2. Discard any θ whose CALIBRATION false-abstention rate exceeds 0.30.
3. Among the rest, choose the θ with the lowest CALIBRATION false-support rate.
4. Ties broken toward the **lower** false-abstention rate; still tied, toward the
   **lower** θ (less aggressive).

The chosen θ is then frozen. VALIDATION compares gate *designs*; TEST is run once
with the frozen θ and the design chosen on VALIDATION.

## 7. Gate designs to compare on VALIDATION

| ID | Conditions |
|---|---|
| **G0** | the Gate 1.5 `grounded()` rule — any query term overlaps the top hit. **Baseline, expected to fail.** |
| **G1** | S1 + S2 (evidence span + distinctive anchor) |
| **G2** | S1 + S2 + S3 (adds literal constraint satisfaction) |
| **G3** | S1 + S2 + S3 + S4 (adds graph relationship grounding) |

G0 is included precisely so the improvement is measured against the rule that
produced false-support = 1.0, rather than against nothing.

## 8. What would falsify this design

- G2/G3 failing to beat G0 on class C → the literal-constraint hypothesis is
  wrong and the design must be reconsidered, not retuned.
- Any design passing safety only by abstaining on most positives → rejected by
  the availability thresholds.
- Any EXPOSE without verified evidence → the gate is not evidence-first and is
  rejected outright.

## 9. Calibration claims

No calibrated probability will be claimed. The gate emits a **decision**, not a
confidence. Score distributions for positives and negatives will be reported as
descriptive statistics only. Calling an uncalibrated score a probability would be
the same category of error as calling model output evidence.

## 10. Prohibited

No LLM in any role: no answer generation, reranking, query expansion, synonym
generation or entity resolution. No vector database. No production query router.
Enforced by the same machine check used in Gate 1.5.
