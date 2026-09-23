# GATE 2.25 REPORT — Trust Boundary + Claim Generalization

**Date:** 2026-09-23 · **Verdict: CONDITIONAL PASS**

Your review was correct: the Gate 2 report described a trust boundary the code
did not enforce. All four gaps are now closed, executable, and covered by 22 new
regression tests. At **224 artifacts / 14,291 claims** the system holds
**0.000 false support with 0 untrusted and 0 unsupported exposures**, and a
400-claim independent sample verifies at **precision 1.000**.

One headline number is **not** trustworthy and I say so in §6: the 0.984 query
parse rate measures my own templates matching my own parser. The honest figure
for unseen phrasing is **0.50**.

Tags: **[MEASURED]** · **[UNPROVEN]** · **[ASSUMED]** · **[BLOCKED]**.

---

## 1. What the review found, confirmed

| Claimed in Gate 2 | Actually in the code |
|---|---|
| "supported = compiled claim with verified evidence" | `establishment` was carried on every claim object and **never consulted** |
| property lookup resolves a property | `children_of` used `LIKE 'entity.%'` — **descendants**, not direct children |
| entity resolution is deterministic | `find_symbols(...)[:4]` — **arbitrary truncation** decided identity |
| conflicts are detected | compared **raw evidence text**; `007` in ADR-007 read as a disagreeing value |

A fifth defect surfaced while fixing these: SQLite `LIKE` is case-insensitive,
so the class `Signer`, the module `signer` and a local variable `signer` were
one entity. Entity matching now uses `GLOB`.

## 2. The boundary, now enforced

Eleven invariants, each naming the one it fails (`CLAIM_TRUST_BOUNDARY.md`).
Enforcement is structural, not documentary:

- `claimfirst.py` **does not import the retriever**. A test parses its **AST**
  (not its text — the docstring legitimately names the signals it refuses) and
  fails on any of `bm25, rank, score, lexical, confidence, r1, r2, overlap, idf`.
- The establishment rule is **single-sourced** in `kgc/predicates.py`; the gate
  consults `allows(predicate, establishment)`, so a predicate cannot be trusted
  at a level its own definition forbids. (Found in the simplification pass: the
  rule had been duplicated.)
- `confidence` is read by **no code path**. A test sets it to 0.97 on an
  untrusted claim and asserts it still cannot expose.

**Untrusted claims are never deleted.** Tests assert `PROPOSED` and `SUPERSEDED`
claims remain queryable after being refused as answers — the database is a
historical store; the support layer decides what may be *presented*.

## 3. Results at scale **[MEASURED]**

corpus3 = **werkzeug @ `6048fa48753c7b61e35cc34537667809dee8fa35`**, BSD-3-Clause,
**224 artifacts**, 1.54 MB, hash `b410ba8a…`. Ingest 8.7 s → 5,303 symbols,
14,291 claims. Query p50 **2.15 ms**, p95 **20.3 ms**.

132 queries across 13 classes, derived by an **independent stdlib-AST pass that
never imports kgc**; negatives are perturbations of verified real facts.

| Split | False support | Untrusted exp. | Unsupported exp. | False abstention | claim R@10 | evidence R@10 | Ambiguity abstention |
|---|---|---|---|---|---|---|---|
| CALIBRATION | **0.000** | 0 | 0 | 0.200 | 0.700 | 0.600 | 1.000 |
| VALIDATION | **0.000** | 0 | 0 | 0.048 | 0.857 | 0.762 | 1.000 |
| **TEST** | **0.000** | **0** | **0** | 0.143 | 0.810 | 0.524 | **1.000** |
| ALL | **0.000** | 0 | 0 | 0.129 | 0.790 | 0.629 | 1.000 |

TEST false support by negative class — every class zero:
`ambiguous_entity 0/2 · source_scope 0/3 · unsupported_entity 0/3 ·
wrong_property 0/3 · wrong_relationship 0/3 · wrong_value 0/4`

Out-of-deterministic-scope queries (prose, rationale, malformed) correctly
abstained: **1.000**. These are scored **separately** — counting prose
abstentions as safety successes would flatter the false-support rate with
questions the system was never asked to answer.

## 4. Independent claim verification **[MEASURED]**

Sampled from the 14,291-claim graph and re-derived by a **separate stdlib
procedure**:

| Claim type | Population | Sample | Confirmed | Precision |
|---|---|---|---|---|
| definition (`CONTAINS` → function/method/class) | 2,197 | 400 | 400 | **1.000** |
| `EXTENDS` | 154 | 150 | 150 | **1.000** |
| evidence spans re-read byte-for-byte | — | 400 | 400 | **1.000** |

Claims lacking verifiable evidence: **0 of 14,291**.

> The first run reported `EXTENDS` precision 0.84. Every disagreement was a
> dotted base (`class X(r.BaseConverter)`) that my **checker** recorded only as
> `ast.Name`. The checker was wrong, not the graph. Recorded because a
> verifier's own bugs are the easiest way to publish a false defect.

## 5. Generalization: corpus1 → corpus2 → corpus3, all retained

| | corpus1 | corpus2 | corpus3 |
|---|---|---|---|
| source | self-authored | itsdangerous | **werkzeug** |
| artifacts | 15 | 23 | **224** |
| false support (claim-first) | — | 0.000 | **0.000** |
| false abstention | — | 0.500 | **0.129** |

No prior corpus was replaced and no prior failure deleted. All earlier
experiment outputs are preserved.

## 6. The number that is not trustworthy **[MEASURED, and it undercuts §3]**

| Query set | n | Parse rate |
|---|---|---|
| Gate 2.25 **template-generated** | 62 | **0.984** |
| Gate 2 **hand-written** (pre-dates this parser) | 20 | **0.500** |

**I wrote both the query templates and the parser.** The templates emit shapes
the parser handles, so 0.984 measures alignment, not coverage. The hand-written
set scores **0.50**, and that is the honest figure for unseen phrasing.

The false-abstention improvement (0.500 → 0.129) **inherits this inflation**. On
hand-written phrasing it would be substantially worse.

Note also: hand-written coverage *fell* from 0.70 under the old parser to 0.50
under the new one, because the stricter IR now refuses queries the old parser
silently mis-answered. That is the safety/availability trade made visible, and
it is the right direction.

## 7. H-3 — what actually needs a model **[MEASURED]**

20 observed deterministic failures, classified:

| Class | n | Share | Meaning |
|---|---|---|---|
| **A** | 6 | **30%** | derivable with a richer claim model — **no model needed** |
| **B** | 6 | 30% | requires document-level semantic interpretation |
| **C** | 3 | 15% | requires reasoning across multiple claims |
| **D** | 5 | 25% | cannot be answered faithfully — refusing is correct |

Class A examples and their minimum capability: *"what is a salt used for"* →
`HAS_PURPOSE` claims from docstrings already extracted; *"what does X re-export"*
→ the `IMPORTS` claims of a package `__init__`, already compiled; *"what does X
raise on failure"* → a `CALLS` claim to an exception class, already compiled.

> **30% of what looks like a language-understanding gap is a claim-model gap.**
> Building a model stage to cover class A would hide a deterministic capability
> behind a probabilistic one. A future model stage should be scoped to **B and
> C only**.

## 8. Answers

1. **Can proposed/disputed claims be guaranteed not to become trusted?**
   **Yes.** Establishment is gated, single-sourced in the predicate spec, and
   covered by four tests. Untrusted exposure at scale: **0**. **[MEASURED]**
2. **Can wrong-property matches be prevented structurally?** **Yes** — property
   lookup traverses `parent_id` (direct children), not a name prefix. A local
   variable inside a method no longer answers as a class attribute.
   `wrong_property` false support on TEST: **0/3**.
3. **Can ambiguous entities cause abstention?** **Yes.** Ambiguity abstention
   **1.000** on every split; truncation removed; a test asserts `[:4]` is gone.
4. **Is conflict determined from claim semantics?** **Yes.** Normalized value
   comparison over a closed domain; outside it the answer is `UNRESOLVED`, never
   a guess. "30 seconds" and "30 s" agree; "30" and "60" contradict.
5. **What predicates are canonical?** Eleven (`CLAIM_SEMANTICS.md`), ten
   structural and one semantic (`HAS_PURPOSE`). Only what the corpora require.
6. **What percentage maps deterministically to the IR?** **0.984 on template
   queries, 0.500 on hand-written.** The second is the real number. **[MEASURED]**
7. **What percentage of claims remain safely verifiable?** **100%** — 0 of
   14,291 lack verifiable evidence; 400/400 sampled spans re-read byte-exact.
8. **Does claim-first keep its safety advantage at ≥200 artifacts?** **Yes** —
   0.000 false support at 224 artifacts, 10× corpus2, across all 52 in-scope
   negatives. **[MEASURED]**
9. **False-abstention at that scale?** **0.129 measured**, but inflated per §6.
10. **New failure modes at scale?** Two. Case-insensitive `LIKE` conflating
    `Signer`/`signer` (fixed, `GLOB`). And evidence R@10 (0.524 on TEST) lagging
    claim R@10 (0.810) — the claim is found but its evidence span is not always
    the one the gold names. **[MEASURED]**
11. **What portion of rationale questions can be compiled without an LLM?**
    **30%** (class A). **[MEASURED]**
12. **Does the neural experiment change the architecture?** **[BLOCKED]** —
    unchanged. No model on disk, host unreachable, LSA not substituted.
13. **What remains unproven?** Parser coverage on unseen phrasing beyond the
    0.50 sample **[UNPROVEN]**; whether the boundary holds on a second language
    **[ASSUMED]**; the neural question **[BLOCKED]**; whether 0.129 false
    abstention survives real phrasing **[UNPROVEN]**.

## 9. Verdict: CONDITIONAL PASS

**Why not FAIL.** The gate's purpose was to prove the boundary is enforced
rather than described. It now is: four gaps closed, a fifth found and fixed, 22
regression tests, 0.000 false support at 10× scale, and independent verification
at precision 1.000 on a 550-claim sample.

**Why not PASS.** One headline number is inflated by my own construction (§6),
and I found it only because the 0.984 looked implausible next to Gate 2's 0.70.
Availability at scale is therefore **not** established. A gate cannot pass on a
metric its author cannot vouch for.

### Conditions

| # | Condition |
|---|---|
| **J-1** | Re-measure availability on **independently authored** queries — written by someone who has not seen the parser, or generated from a source that is not mine. The 0.129 is not usable until then. |
| **J-2** | Close the evidence-localization gap: claim R@10 0.810 vs evidence R@10 0.524 on TEST. The claim is found; its gold-named span often is not. |
| **J-3** | Compile the class-A claim types (`HAS_PURPOSE` from docstrings, re-export → `IMPORTS`, raise → `CALLS`). 30% of apparent semantic need is a claim-model gap, and closing it shrinks what any model is later asked to do. |
| **J-4** | Exercise the boundary on a second language before claiming language independence. |
| **J-5** | Run the external neural experiment or close the question permanently. |

## 10. Still prohibited

LLM in every role · vector database · graph database migration · production
query router · UI · multimodal · provider adapters · distributed anything.

## 11. Reproducing

```bash
python3 experiments/validate_gate225_gold.py          # gold integrity
python3 experiments/gate225_eval.py                   # §3
python3 experiments/independent_claim_verification.py # §4
python3 experiments/h3_rationale_classification.py    # §7
python3 -m unittest discover -s tests -t .            # 62 tests
```

Preserved, never rewritten: `x1_results.json`, `abstention_results_RUN{1,2,3}*.json`,
`claim_first_results_RUN1.json`, `c2_generalization_results.json`.
