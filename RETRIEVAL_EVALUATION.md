# X-1 RETRIEVAL EVALUATION

**Date:** 2026-09-22 · **Pre-registration:** `eval/PREREGISTRATION.md` (commit `d99756e`,
which precedes every line of retrieval code) · **Raw:** `experiments/x1_results.json`

**Headline: no configuration cleared its pre-registered adoption bar, and every
configuration except R0 was disqualified by rule D-C. The central question —
whether dense vectors are required — remains UNANSWERED, partly because of a
weakness in my own experiment design.**

Separation was machine-checked: the retrieval modules reference no gold file and
no LLM. Zero LLM calls were made anywhere in this gate.

---

## 1. Configurations and cost

| | Method | Index build | Model download | p50 | p95 |
|---|---|---|---|---|---|
| R0 | exact identifier / path | — | 0 MB | 0.01 ms | 0.23 ms |
| R1 | BM25 (SQLite FTS5) | 0.010 s | 0 MB | 0.07 ms | 0.21 ms |
| R2 | R1 + bounded ranked graph | 0.010 s | 0 MB | 0.33 ms | 0.58 ms |
| R3 | dense (LSA, 64 dims) | 0.059 s | **0 MB** | 0.04 ms | 0.06 ms |
| R4 | R3 + R1 | 0.069 s | 0 MB | 0.09 ms | 0.14 ms |
| R5 | R3 + R1 + graph | 0.069 s | 0 MB | 0.26 ms | 0.36 ms |

87 chunks. Everything runs offline in-process; no vector server was built.

## 2. Overall results (21 answerable queries)

| | R@1 | R@5 | R@10 | MRR | evidence@10 | evidence gap | false support | conflict kept | distractor beats gold |
|---|---|---|---|---|---|---|---|---|---|
| R0 | 0.238 | 0.333 | 0.333 | 0.278 | 0.286 | 0.048 | **0.0** | 0.0 | 0.0 |
| **R1** | 0.429 | 0.905 | **0.952** | 0.609 | **0.810** | 0.143 | 1.0 | 1.0 | **0.0** |
| R2 | 0.429 | **0.952** | 0.952 | 0.574 | 0.810 | 0.143 | 1.0 | 1.0 | **0.0** |
| **R3** | **0.476** | 0.857 | 0.905 | **0.623** | 0.762 | 0.143 | 1.0 | 1.0 | 1.0 |
| R4 | 0.429 | 0.857 | 0.952 | 0.593 | 0.810 | 0.143 | 1.0 | 1.0 | 1.0 |
| R5 | 0.333 | 0.857 | 0.952 | 0.507 | 0.810 | 0.143 | 1.0 | 1.0 | 1.0 |

**Recall@10 saturates** — R1 through R5 are all 0.952. It does not discriminate on
a corpus this size, so the analysis below uses R@1 and MRR.

### MRR by class (the discriminating view)

| class | R0 | R1 | R2 | R3 | R4 | R5 |
|---|---|---|---|---|---|---|
| exact_identifier | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| structural_code | 0.42 | 0.75 | **1.00** | 0.42 | 0.50 | 0.62 |
| evidence_localization | 0 | **1.00** | 0.60 | 0.75 | **1.00** | 0.38 |
| cross_document | 0.50 | 0.55 | 0.62 | **1.00** | 0.62 | 0.67 |
| entity_ambiguity | 0.50 | 0.50 | 0.25 | **1.00** | 0.75 | 0.35 |
| lexical | 0 | 0.67 | 0.62 | 0.67 | 0.67 | 0.62 |
| conflict | 0 | 0.50 | 0.50 | 0.50 | 0.33 | 0.33 |
| paraphrase | 0 | **0.50** | **0.50** | 0.44 | 0.44 | 0.44 |
| multi_hop | 0 | 0.23 | 0.23 | 0.23 | 0.23 | 0.23 |
| adversarial_misleading_lexical | 0 | **0.20** | **0.20** | 0 | 0.12 | 0.12 |
| adversarial_structural_not_lexical | 0 | **0.20** | **0.20** | 0.12 | 0.12 | 0.12 |

No configuration dominates. **R2 is the only one that solves structural code
queries (MRR 1.00); R3 is the only one that solves cross-document and entity
ambiguity (1.00 each); R1 is the only one that solves evidence localization
(1.00).** Combining them (R5) is the *worst* hybrid at MRR 0.507 — fusion
diluted each method's strength rather than compounding it.

## 3. Decisions against the pre-registered rules

### D-A — graph traversal (R2 over R1): **NOT MET**

Required `ΔR@10 ≥ 0.05` on `multi_hop`, `structural_code` or `cross_document`.
Measured `Δ = 0.000` on all three — they were already at Recall@10 = 1.0 under
R1. **The rule could not be satisfied on this corpus**, which is a defect in the
experiment, not evidence against graph traversal.

Under a sensitive metric the graph clearly helps where it should:
`structural_code` MRR **0.75 → 1.00**, and Q10-a moves from rank 2 to rank 1.
It also *hurts* elsewhere: `entity_ambiguity` MRR 0.50 → 0.25,
`evidence_localization` 1.00 → 0.60, net MRR 0.609 → 0.574.

**Conclusion: graph expansion is a per-query-class tool, not a global booster.**
Applying it to every query made retrieval worse overall.

### D-B — dense retrieval: **NOT MET, and INCONCLUSIVE for neural models**

Required `ΔR@10 ≥ 0.10` on paraphrase. Measured **0.000** (R1 = R3 = 0.667).

Per the pre-registration, a negative LSA result **must not** be reported as
"dense retrieval doesn't help." The reason is now concrete rather than
precautionary — see §4.

### D-C — false support: **R1–R5 ALL DISQUALIFIED**

`false_support_rate = 1.0` for every configuration except R0.

Both unanswerable queries returned confident top-ranked results:

| query | top-3 returned |
|---|---|
| "what is the Kafka topic used for order events" | `orderflow/payments.py`, `data/routing.xml`, `data/fees.csv` |
| "which GDPR data retention period applies to cardholder names" | `orderflow/reporting.py`, `docs/architecture.md`, `orderflow/reporting.py` |

The corpus contains no Kafka and no GDPR. Every ranking retriever returned its
top-k anyway, because **ranking has no concept of "nothing here is relevant."**

*Is the rule fair?* The uniform abstention test was "does the top hit share a
content term with the query." Q7-a shares "order" with `orderflow`. A stricter
rule would abstain correctly here. But the finding survives any reasonable
operationalisation: **a pure ranking retriever always returns its top-k, so
abstention cannot be a property of ranking.** It requires a separate gate —
which this project needs anyway, since answering without evidence is precisely
what it forbids.

### D-D — evidence gap: **PASSES, narrowly**

Gap of 0.143 against a 0.15 threshold for R1–R5. Answer-correct@10 = 0.952 while
evidence-correct@10 = 0.810: **on roughly 1 query in 7, the right file is
retrieved but the span that actually supports the answer is not.**

### D-E — simplicity tie-break: not reached (no configuration qualified).

## 4. Why the paraphrase result is inconclusive — a defect in my experiment

Of three paraphrase queries, **only one has genuinely zero vocabulary overlap**
with the corpus:

| query | in-corpus terms | out-of-vocabulary |
|---|---|---|
| Q3-a "stop waiting for a stalled purchase" | waiting, stalled, purchase | stop |
| Q3-b "shoppers walk away from a slow checkout" | shoppers, walk, away, slow, checkout | — |
| **Q3-c "how is the bookkeeping of funds handled"** | **none** | **bookkeeping, funds, handled** |

Q3-a and Q3-b share vocabulary with their answers, so BM25 solves them and they
do not test paraphrase at all. Only Q3-c does — **and every configuration
returned an empty result set for it.**

The reason is structural, not incidental:

> **LSA's vocabulary is derived from the corpus.** A query whose every term is
> out-of-vocabulary projects to the zero vector, so R3 cannot return anything.
> It fails Q3-c *by construction*, not by weakness.
>
> This is exactly the case where a **pretrained neural embedding would differ**:
> its semantic space is learned from general text, so "bookkeeping" and "funds"
> have representations near "ledger" and "money movements" regardless of whether
> those words appear in this corpus.

So the one query that genuinely tests the dense hypothesis is the one LSA cannot
answer in principle. **The dense-vector question is not answered by this
experiment**, and the pre-registered "inconclusive" clause applies with a
specific mechanism behind it rather than as a hedge.

## 5. Adversarial results

| Attack | Result |
|---|---|
| Misleading lexical ("processor deadline" → `reporting.py` is the trap) | **R1/R2 resist (distractor never beats gold). R3/R4/R5 fall for it every time (1.0).** |
| Structurally related, lexically unrelated ("abandonment threshold") | all configurations weak (MRR ≤ 0.20) |
| Ambiguous entity names (`Processor`) | R3 best (MRR 1.00); graph *hurt* it (R2 = 0.25) |
| Conflicting documents | R1–R5 all return **both** sides (conflict preservation 1.00) |
| Stale documentation | `runbook.md` retrieved alongside current sources, not instead of them |
| Unsupported question | **all ranking configurations fabricate support** (§D-C) |
| Silent entity merge | **none** — `payments.PaymentProcessor` and `reporting.Processor` remain distinct symbols |
| Hub / graph explosion | bounded; see §6 |

The dense-vs-lexical split on the misleading-lexical attack is worth stating
plainly: **adding dense retrieval made the adversarial case strictly worse**,
from 0.0 to 1.0 distractor-beats-gold.

## 6. D-1 graph expansion attack, re-run under ADR-0007

Whole repository, real hub structure (`kgc.pipeline.ingest` has in-degree 44):

| node | depth | edge kinds | returned | total reachable | truncated | ms |
|---|---|---|---|---|---|---|
| ordinary | 2 | all | 10 | 50 | yes | 13.7 |
| ordinary | 4 | CALLS-only | 10 | 88 | yes | 16.8 |
| ordinary | 4 | all | 10 | 405 | yes | 67.9 |
| hub | 2 | all | 10 | 359 | yes | 56.6 |
| hub | 4 | CALLS-only | 10 | 129 | yes | 24.7 |
| **hub** | **4** | **all** | **10** | **792** | **yes** | **121.6** |

| Policy check | Result |
|---|---|
| never returns more than k | **true** |
| `total_reachable` always disclosed | **true** |
| truncation flagged whenever reachable > k | **true** |
| edge filtering materially changes the reachable set | **true** (129 vs 792 at hub depth 4) |
| hub expansion bounded | **true** |
| max latency | 121.6 ms |

**D-1 is fixed.** The case that previously returned 50,197 unranked nodes now
returns 10 ranked nodes and states the true total. No result silently claims
completeness.

## 7. Failed experiments, stated plainly

| What failed | Why | Fundamental or implementation? |
|---|---|---|
| Dense retrieval on paraphrase | LSA vocabulary is corpus-derived; OOV queries project to zero | **Fundamental to LSA**, not to dense retrieval. Untested for neural models. |
| Neural embedding test | `huggingface.co` unreachable (HTTP 000, measured twice) | **Environmental.** Declared before running, not discovered after. |
| D-A graph threshold | Recall@10 saturated at 1.0 under R1, so no improvement was expressible | **Experiment design.** Corpus too small; threshold should have been on MRR. |
| Paraphrase class as a test | 2 of 3 queries share vocabulary with their answers | **My authoring error.** The class does not test what it names. |
| Hybrid fusion (R5) | Reciprocal-rank fusion diluted each method's strengths | **Implementation-specific**; per-class routing was not tried (and would need its own pre-registration). |
| Abstention on unanswerable queries | ranking has no "nothing is relevant" state | **Fundamental to retrieval alone.** Needs a separate gate. |

Nothing here was retried with a different method after seeing the result. The
configurations were fixed in advance and run once.

## 8. What the measurements actually support

1. **BM25 + query preprocessing (R1) is the strongest single configuration** on
   evidence correctness (0.810) and adversarial robustness, at 0.010 s index
   build and zero dependencies.
2. **Query preprocessing was essential, and its absence was the real D-2 defect.**
   FTS5's implicit AND returned zero hits for paraphrases; OR semantics plus
   identifier splitting fixed that without any alias table.
3. **Graph expansion belongs on structural queries only** (MRR 0.75 → 1.00), and
   degrades others. It should be routed by query class, not applied globally.
4. **No evidence justifies a vector store.** Not because dense retrieval lost —
   because the experiment that would decide it could not be run here.
5. **Retrieval alone cannot refuse to answer.** This is the most important result
   in this gate, and it was not on the list of things I expected to find.
