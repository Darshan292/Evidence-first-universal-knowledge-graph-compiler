# GATE 1.75 REPORT — Retrieval Safety + Experiment Repair

**Date:** 2026-09-22 · **Verdict: FAIL**

The abstention gate **missed its pre-registered safety threshold on every TEST
run**, by a factor of 2.5× to 5×, and the TEST split is now contaminated by three
observations. The X-1 repair succeeded and produced the measurement Gate 1.5
failed to produce. The architecture is not refuted — one component and the
held-out data must be rebuilt.

Tags: **[MEASURED]** · **[UNPROVEN]** · **[ASSUMED]** · **[BLOCKED]** (environmental).

---

## 1. Correction carried out (item 0)

The Gate 1.5 claim *"a ranking retriever cannot abstain"* is **withdrawn**. The
statement now carried in `ABSTENTION_DESIGN.md` §0:

> A raw top-k ranking stage does not by itself express "no supported result"; a
> separate support/abstention decision is therefore required. The ranker proposes
> candidates; a distinct gate decides whether any are supported enough to expose.

The architecture is now explicitly `query → candidates → support evaluation →
ABSTAIN | EXPOSE | EXPOSE_CONFLICTED`, with retrieval correctness and support
correctness measured separately and never merged.

A third outcome was added during design, before measurement: a binary gate forces
a wrong answer when a value is genuinely present in a *stale, contradicted*
source. Abstaining denies known information; exposing silently asserts a
contradicted value. `EXPOSE_CONFLICTED` returns both sides with the conflict
marked.

## 2. Abstention results

### TEST was run three times. All three are reported.

| Run | Status | Design | θ | False support | Class C | False abstention | Acceptance | Unsupported exposure |
|---|---|---|---|---|---|---|---|---|
| **#1** | **INVALID** (implementation defect) | G1 | 60 | 0.125 | 0.0 | **1.00** | **0.00** | 0 |
| **#2** | **BINDING** | G2 | 90 | **0.250** | **0.000** | **0.600** | 0.400 | 0 |
| **#3** | POST-HOC (contaminated) | G2 | 90 | **0.500** | 0.500 | 0.200 | 0.800 | 0 |
| | **Pre-registered threshold** | | | **≤ 0.10** | **≤ 0.15** | **≤ 0.30** | **≥ 0.70** | **= 0** |

Run #1 was invalidated by a defect found after running it: θ was a percentile over
the *corpus* vocabulary, and 46.3% of this corpus's vocabulary is hapax, so p60
landed on the maximum IDF. "Distinctive" collapsed to "appears in exactly one
chunk", excluding ordinary query terms (`payment`, IDF 1.9), and an empty
distinctive set returned `False`. The gate abstained on everything — the exact
outcome the pre-registration declared invalid.

Run #3 applied three repairs (design G4's stemming fix propagated to G2). It is
**post-hoc and contaminated**: it was designed after seeing TEST. It is reported
because the pre-registration requires every TEST run to be reported, and it is
**not** the binding result.

**Run #2 is binding, and it fails.** False support 0.250 against a ≤0.10
threshold; false abstention 0.600 against ≤0.30.

### What did work **[MEASURED]**

| Design | False support (VALIDATION) | Class C |
|---|---|---|
| **G0** — the Gate 1.5 `grounded()` rule | **0.889** | **1.000** |
| G1 — evidence + distinctive anchor | 0.333 | 0.500 |
| **G2** — + literal constraint (S3) | **0.222** | **0.000** |
| G3 — + graph relation grounding (S4) | 0.222 | 0.000 |

**The literal-constraint hypothesis is confirmed.** The hard-negative class C —
queries sharing nearly all vocabulary with the true source but requesting an
unsupported fact — goes from **1.000 false support under the Gate 1.5 rule to
0.000** the moment S3 is added. *"Why is the payment gateway deadline 90
seconds"* is rejected because `90` does not appear in the evidence, even though
every other signal passes it.

**Unsupported exposure was 0 in all three runs** — no candidate was ever exposed
without a localized evidence span. The founding invariant held.

### Why it still fails **[MEASURED]**

The failures are concentrated in S2, and both directions were diagnosed:

| Failure | Cause |
|---|---|
| `N-H3` *"fee_bps for APAC visa"* exposed | The exposed candidate matched `apac` in **routing.xml** while the question was about **fees.csv**. **Per-candidate support is not support for the query.** |
| `N-F3` *"timeout of the reporting Processor"* exposed | Only `reporting` was distinctive at θ=90; S2 requires *any* distinctive term, so a file containing `reporting` sufficed. |
| `P-06` *"which module imports SETTLEMENT_BATCH_SIZE"* abstained | S2 compared **unstemmed** tokens while the index used a porter tokenizer: query `imports` never matched source `import`. |
| Spurious `EXPOSE_CONFLICTED` | S5 counted *any* two numbers as disagreement, including the `007` in ADR-007. |

All four are implementation defects with known fixes, not refutations of the
design. But they were found *by running TEST*, which is why TEST is now spent.

## 3. X-1 repair — the measurement Gate 1.5 owed

`X1-RERUN-01` is **additive**; `experiments/x1_results.json` is frozen and
unmodified.

**Authoring was machine-verified, and the machinery caught me.** Of the first 8
queries I wrote as "zero overlap", **5 were rejected** for sharing a stemmed term
with their source — the identical error I made in Gate 1.5, this time caught
before measurement instead of after. The final set has **10 verified
zero-overlap**, 4 partial, 3 full-overlap controls, and **0 rejections**.

### Results **[MEASURED]**

| Retriever | zero_overlap R@10 | partial R@10 | full R@10 | empty result sets (zero) |
|---|---|---|---|---|
| R1 BM25 | **0.1** | 1.0 | 1.0 | **5 / 10** |
| R3 LSA | **0.2** | 1.0 | 1.0 | **5 / 10** |
| R4 BM25+LSA | **0.3** | 1.0 | 1.0 | 5 / 10 |
| R2 BM25+graph | 0.1 (R@1 **0.1**) | 1.0 | 1.0 | 5 / 10 |

**This answers the Gate 1.5 question that was left UNCERTAIN: lexical retrieval
fails materially on genuine paraphrase.** Recall@10 collapses from 1.0 to 0.1
when vocabulary overlap reaches zero, and half the queries return *nothing at
all*. The gradient across the three tiers is clean, which is what a properly
constructed paraphrase class looks like.

Corpus-derived LSA adds almost nothing (0.1 → 0.2) and cannot in principle: its
vocabulary is the corpus, so an out-of-vocabulary query projects to the zero
vector.

One quiet result worth keeping: **R2's graph expansion reached a zero-overlap
target at rank 1** (R@1 = 0.1 where every lexical method scores 0.0). Structural
expansion can reach an answer that shares no vocabulary with the question. One
query is not a finding, but it is the right place to look next.

## 4. Neural embedding experiment — **[BLOCKED]**

Environment inspected: no `ollama`, no model caches, **no `*.gguf` / `*.onnx` /
`*.safetensors` anywhere on disk**, no embedding runtime installed,
`huggingface.co` unreachable (HTTP 000, confirmed twice).

**No local model exists, so the run did not happen, and LSA was not substituted a
second time.** Delivered instead: `experiments/neural/` with a self-contained
runner, exact model identifier (`BAAI/bge-small-en-v1.5`, 384 dims, ~130 MB),
mandatory file hashing that warns when a result cannot be attributed to a
revision, the output schema, and a decision rule fixed in advance —
**zero-overlap Recall@10 ≥ 0.8** against the now-measured 0.1 baseline.

## 5. Evidence localization — diagnosed, not patched **[MEASURED]**

21 queries carry an evidence requirement; **4 fail (19.0%)**. Cause breakdown:

| Cause | n | Meaning |
|---|---|---|
| `CHUNK_NOT_RETRIEVED` | **3** | the right file ranks (twice at #1), the chunk holding the evidence **exists and is correctly formed**, but ranks outside the top-10 |
| `WRONG_FILE` | 1 | Q3-c, the zero-overlap paraphrase that returns nothing |
| `CHUNK_TOO_NARROW` | 0 | — |
| `GOLD_STRING_ISSUE` | 0 | — |

**The cause is retrieval granularity, not chunking.** For Q5-a the file ranks
#1 and `adr-007#5` holds `"18 seconds"` — it simply never entered the top-10
because it matched the query terms less well than its sibling chunks.

**Minimum correction:** localize evidence by a *second pass within already
retrieved files*, rather than requiring the evidence-bearing chunk to win the
global ranking. **Chunking was not changed**, per instruction — it is not at
fault.

## 6. Graph routing finding — preserved, not implemented

Unchanged and not built into a router (item 14). Evidence that would justify one
later, stated now: a query-class split where graph expansion shows
`ΔMRR ≥ +0.15` on structural queries **and** `ΔMRR ≥ −0.02` elsewhere, measured
on a corpus where Recall@10 does not saturate. Current evidence: structural
`0.75 → 1.00`, entity-ambiguity `0.50 → 0.25`.

D-1 was **not** re-run: the abstention work did not touch the traversal path, and
the Gate 1.5 bounded-expansion evidence stands as the baseline (item 16).

## 7. Answers

### Abstention
1. **Can unsupported questions be rejected deterministically?** **Partly.** 6 of 8
   TEST negatives were correctly rejected in run #2, with zero LLM involvement.
   But 0.250 false support fails the ≤0.10 bar. **[MEASURED]**
2. **Can lexically overlapping but unsupported questions be rejected?** **Yes —
   this is the clearest result.** Class C: **1.000 → 0.000** false support once
   the literal-constraint condition is added. **[MEASURED]**
3. **False-support rate achieved?** **0.250** (binding run #2); 0.500 post-hoc.
   Threshold was 0.10. **[MEASURED]**
4. **False-abstention introduced?** **0.600** (binding); 0.200 post-hoc.
   Threshold was 0.30. **[MEASURED]**
5. **Does the gate preserve useful recall?** Retrieval is untouched by
   construction — the gate sits after ranking. But acceptance of 0.400 means the
   *system* surfaces far less than it retrieves. **Not acceptable as it stands.**

### Dense retrieval
6. **Was a genuine neural model tested?** **No — [BLOCKED].** No model on disk,
   host unreachable, confirmed twice.
7. **Did it improve zero-overlap paraphrase?** **[UNPROVEN.]** The gap is now
   precisely quantified (BM25 0.1, LSA 0.2, target 0.8) so the external run has a
   sharp target.
8. **Evidence correctness?** **[UNPROVEN.]**
9. **New adversarial failures?** **[UNPROVEN]** for neural. For LSA, Gate 1.5
   measured distractor-beats-gold going 0.0 → 1.0 — which is why it is condition
   3 of the external decision rule.
10. **Gain worth the cost?** **[UNPROVEN.]** Not answerable here, and not
    guessed.

### Evidence
11. **Why the gap?** **Retrieval granularity.** 3 of 4 failures are
    `CHUNK_NOT_RETRIEVED`: right file, correctly-formed chunk, ranked out of
    top-10. **[MEASURED]**
12. **Minimum correction?** A within-file evidence-localization pass over already
    retrieved files. No chunking change. No new dependency.

### Architecture
13. **Simplest evidence-safe architecture now?** BM25 + query preprocessing for
    candidates → **support gate whose mandatory conditions are a verified
    evidence span and literal-constraint satisfaction** → within-file evidence
    localization → graph expansion routed to structural queries only.
    **This is the shape; the S2 component is not yet fit to ship.**
14. **Intentionally absent:** vector database, embedding model as a dependency,
    query router, LLM in any role, global hybrid fusion, UI, provider adapters,
    Tree-sitter as validity oracle, SCIP, archives.
15. **Still genuinely unanswered:** whether a neural embedding closes the
    zero-overlap gap **[BLOCKED]**; whether requiring *all* distinctive query
    terms fixes false support without destroying availability **[UNPROVEN]**;
    whether any of this holds beyond an 87-chunk self-authored corpus
    **[ASSUMED]**.

## 8. Verdict: FAIL

**Why FAIL and not CONDITIONAL PASS.** The gate's purpose is safety. It missed
its pre-registered safety threshold on **every** TEST run, by 2.5× on the binding
run and 5× post-hoc, while also failing availability. A gate that exposes a
quarter of unsupported queries is not conditionally usable — it is not usable.
Grading it as a conditional pass because the mechanism is promising would be
exactly the self-deception this project is built to prevent.

**What FAIL does not mean.** The architecture is not refuted. The support-gate
separation is sound, the evidence invariant held in all three runs, and the
central hypothesis — that literal-constraint checking defeats lexically
overlapping negatives — is **confirmed** (class C 1.000 → 0.000). Every failure
is localized to the S2 anchoring condition and has a known fix.

**The methodological cost is the real damage.** Three TEST observations mean the
TEST split is burned. Any further iteration on this gate **requires a fresh
held-out set**; tuning against the current one would be contamination however
carefully it were dressed up.

### Conditions to re-enter this gate

| # | Condition |
|---|---|
| **F-1** | Build a **new held-out TEST split** from newly authored queries. The current TEST split is spent and must be retired, not reused. |
| **F-2** | Replace S2: require coverage of the query's distinctive terms **within a single candidate**, stemmed. Both binding failures (`N-F3`, `N-H3`) and the main false abstention (`P-06`) trace to this one condition. |
| **F-3** | Re-scope S5 conflict detection to numerals adjacent to a shared distinctive term. `007` must never read as a disagreeing timeout value. |
| **F-4** | Implement the within-file evidence localization pass (§5) and re-measure the 19% gap. |
| **F-5** | Run the external neural experiment, or record permanently that the dense question is unresolved and ship lexical-only. |

## 9. Still prohibited

LLM in any role · vector database · production query router · UI · multimodal ·
provider adapters · Step 4 implementation.

## 10. Reproducing

```bash
python3 experiments/abstention_benchmark.py            # calibrate / validate / test
.evalenv/bin/python experiments/x1_rerun_01.py         # X1-RERUN-01
python3 experiments/evidence_localization_analysis.py  # §5 diagnosis
python3 -m unittest discover -s tests -t .             # 40 Gate 1 tests
# external only, needs model access:
python3 experiments/neural/run_neural_experiment.py --model BAAI/bge-small-en-v1.5
```

Frozen artifacts: `experiments/abstention_results_RUN1_INVALID.json`,
`..._RUN2_BINDING.json`, `..._RUN3_POSTHOC.json`, `experiments/x1_results.json`
(original X-1, unmodified).
