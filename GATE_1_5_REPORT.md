# GATE 1.5 REPORT — Evaluation Substrate + Retrieval

**Date:** 2026-09-22 · **Verdict: CONDITIONAL PASS**

Conditions C-1, C-2 and C-3 are **resolved**. The retrieval experiment ran as
pre-registered and produced a clear result — but the clear result is that **the
central question was not answered**, for reasons partly environmental and partly
my own experiment design. One finding I did not anticipate is more important than
the one I set out to test.

Tags: **[MEASURED]** executed and recorded · **[DECISION]** a choice made here ·
**[ASSUMPTION]** believed, unverified · **[UNCERTAIN]** unresolved.

---

## 1. Conditions resolved

| | Status | Evidence |
|---|---|---|
| **C-1** identifier storage | **RESOLVED** — 128-bit hex TEXT adopted | 32.2% smaller on the real pipeline, 2.5× insert throughput, no latency cost. `IDENTIFIER_STORAGE_DECISION.md` |
| **C-2** cross-module resolution | **RESOLVED** — resolver implemented | deterministic recall **0.222 → 1.000**, unresolved-correct **0.000 → 1.000**, **0** false positives, 167 cross-file edges on self-ingest. `CODE_ANALYSIS_EVALUATION.md` |
| **C-3** code-analysis boundary | **RESOLVED** — contract defined | `ast` is the validity oracle; Tree-sitter deferred to a recovery-only role, because it fails to flag 2 of 5 invalid files. `CODE_ANALYSIS_EVALUATION.md` |

## 2. Integrity of the experiment

- Corpus, gold and decision rules were **committed before any retrieval code
  existed** (`d99756e`). The git history is the evidence, not an assurance.
- Separation was **machine-checked**: no retrieval module references any gold
  file, and none references an LLM.
- **Zero LLM calls** were made in this gate.
- No alias table, synonym list or query-specific rule was used. The 5/5 alias
  demonstration from the Phase-0 review was **not** reused — it was a mechanism
  demonstration and stayed excluded.
- One gold correction was made and versioned with its reason, before scoring.

## 3. Defects found and fixed during this gate

| # | Defect | How found |
|---|---|---|
| G15-1 | Imports of absent packages (`requests`, `os`) were labelled `HEURISTIC` — claiming more than was established | C-2 gold measurement |
| G15-2 | Resolver produced `DETERMINISTIC` references with a **null target** (the mapper could only link within one artifact) | C-2 re-measurement; now blocked by a new database invariant |
| G15-3 | **Non-Python files produced no record at all** — `seen: 0`. The coverage report would have claimed total success on a corpus it silently ignored | C-3 category D |
| G15-4 | Module naming is root-relative, so ingesting a package directory instead of its parent silently loses **every** cross-module edge | measured 0 vs 167 cross-file edges |

G15-3 and G15-4 are both silent-failure modes — the class this project exists to
eliminate.

## 4. Answers to the fifteen gate questions

### Code analysis

**1. What deterministic code facts can we guarantee?** For Python: definitions,
containment, imports, docstrings and call sites, each with a byte-exact evidence
span, under the normative CPython grammar; plus cross-module resolution within
the analysed corpus (alias, from-, re-export, circular, shadowing) with **zero**
wrong deterministic targets on gold. Every file is accounted for as `OK`,
`FAILED`, `SKIPPED` or `UNSUPPORTED` — never absent.

**2. What remains heuristic?** Re-exports resolved through a package `__init__`
(one hop of indirection). On real code, 6.4% of references. **[MEASURED]**

**3. What remains unresolved?** 75.1% of references on this repository: method
calls on local variables, builtins, computed call targets, and anything outside
the analysed corpus. Each is recorded with its surface name and a reason.
**[MEASURED]** The rate *rose* from 56.1% when the resolver was added, because
imports previously flattered as `HEURISTIC` are now honestly `UNRESOLVED`.

**4. Is cross-module resolution good enough for retrieval evaluation?**
**Yes.** 1.000 deterministic recall on gold, zero false positives, and 167 real
cross-file edges on self-ingest. The graph now has the structural edges that
multi-hop retrieval needs — which is what C-2 was blocking on.

**5. What minimum contract should the first release promise?** Exactly the five
guarantees in `CODE_ANALYSIS_EVALUATION.md` §"minimum contract" — Python only,
no types, no dynamic dispatch, no other language. Anything more would be a claim
the measurements do not support.

### Retrieval

**6. Does lexical retrieval fail materially on paraphrase?** **UNCERTAIN, and I
will not claim otherwise.** Only 1 of my 3 paraphrase queries has genuinely zero
vocabulary overlap; the other two share terms and are solved by BM25. On the one
real test, *every* configuration returned nothing. The class does not test what
it names — my authoring error. **[UNCERTAIN]**

**7. Does graph traversal materially improve recall?** **Not by the
pre-registered rule (D-A not met)** — Recall@10 was already 1.0 on the target
classes, so no improvement was expressible. On MRR it clearly helps where it
should (`structural_code` **0.75 → 1.00**) and clearly hurts elsewhere
(`entity_ambiguity` 0.50 → 0.25), for a **net MRR decline** 0.609 → 0.574.
**[MEASURED]** Graph expansion is a per-class tool, not a global booster.

**8. Does dense retrieval materially improve recall?** **No, by LSA — and that
is inconclusive for neural models.** R3 had the best R@1 (0.476) and MRR (0.623)
and was the only configuration to solve `cross_document` and `entity_ambiguity`
(MRR 1.00 each), but gained nothing on paraphrase and **made the adversarial
misleading-lexical case strictly worse** (distractor beats gold 0.0 → 1.0).
**[MEASURED]**

**9. What does dense retrieval cost?** LSA: 0.059 s index build, 64 dimensions,
**0 MB download**, 0.06 ms p95 query. Negligible. A neural model would cost
roughly 90–130 MB plus embedding time — **[UNCERTAIN]**, unmeasured here.

**10. Does the improvement justify the complexity?** **Not on this evidence.**
No configuration cleared D-B, and every configuration except R0 was disqualified
by D-C. But "not justified" here means "not demonstrated", not "refuted".

### Evidence

**11. Can retrieval preserve evidence correctness?** Largely. Evidence-correct@10
is 0.810 for R1/R2/R4/R5 against answer-correct@10 of 0.952. **[MEASURED]**

**12. How often is the source correct but the localization wrong?** **14.3% —
roughly one query in seven.** Within the 0.15 D-D threshold, but narrowly, and it
is the metric that matters most for a system whose product is evidence.
**[MEASURED]**

### Architecture

**13. What is the simplest architecture supported by the measurements?**
**R1 — BM25 over SQLite FTS5, with fixed query preprocessing — plus graph
expansion routed to structural queries only.** R1 has the best evidence
correctness, the best adversarial robustness, a 0.010 s index build and zero
dependencies. Graph expansion earns its place on `structural_code` (MRR 1.00)
and must not be applied globally.

**14. What should explicitly NOT be introduced yet?** A vector store or vector
server; any embedding model as a required dependency; global hybrid fusion (R5
was the *worst* hybrid, MRR 0.507); LLM query expansion or reranking;
Tree-sitter as a validity oracle; SCIP; any second storage engine.

**15. What assumptions remain unproven?**
- Whether a **neural** embedding closes the paraphrase gap. **[UNCERTAIN]** —
  the decisive query is one LSA cannot answer by construction.
- Whether these results hold beyond an 87-chunk corpus I wrote myself.
  **[ASSUMPTION]**
- Whether per-class retrieval routing beats a single pipeline. **[UNCERTAIN]** —
  suggested by the per-class table, not tested, and would need its own
  pre-registration.
- Whether the abstention gate (below) can be built without an LLM.
  **[UNCERTAIN]**

## 5. The finding I did not go looking for

Every ranking configuration fabricated support for both unanswerable queries
(`false_support_rate = 1.0`). Asked about Kafka in a corpus with no Kafka, R1
confidently returned `orderflow/payments.py`.

This is not a tuning problem. **A ranking retriever always returns its top-k;
"nothing here is relevant" is not a state it can express.** Abstention has to be
a separate gate — and for this project that gate is not optional, because
answering without evidence is the specific failure the whole architecture exists
to prevent.

It is also the one finding here that generalises beyond my corpus.

## 6. Verdict: CONDITIONAL PASS

**Passed:** all three Gate 1 conditions resolved with measurements; the
evaluation substrate exists, is frozen, and is integrity-checked; the D-1
expansion attack is fixed and verified against a real hub graph; the code
analysis contract is defined and met; 40 Gate 1 tests still pass.

**Conditions before Step 4 (retrieval implementation):**

| # | Condition |
|---|---|
| **E-1** | **Build the abstention gate before any answering layer.** No LLM. Until then the system must not present retrieval results as supported answers. |
| **E-2** | **Re-run X-1 with a corpus large enough that Recall@10 discriminates, and a paraphrase class where every query has verified zero vocabulary overlap.** The current class does not test what it names. |
| **E-3** | **Settle the dense question on a machine with model access**, or record permanently that it is unsettled and ship lexical-only. Do not infer it from the LSA result. |
| **E-4** | **Implement graph expansion as per-class routing, not a global stage** — measured to help `structural_code` and hurt `entity_ambiguity`. |
| **E-5** | **Close the 14.3% evidence-localization gap**, or state it as a published limitation. |

**Not blocking, but named:** module naming is root-relative and silently loses
all cross-module edges when a package directory is ingested instead of its parent
(G15-4); the system should detect and warn, and does not.

## 7. Still blocked

LLM enrichment · provider adapters · embeddings as a dependency · vector stores ·
graph UI · multimodal · Docling · SCIP · archives · distributed execution ·
Step 4 implementation.

## 8. Reproducing

```bash
python3 experiments/validate_gold.py          # gold integrity
python3 experiments/c1_identifier_storage.py  # C-1
python3 experiments/c2_resolution_eval.py     # C-2
.evalenv/bin/python experiments/c3_code_analysis.py     # C-3
.evalenv/bin/python experiments/x1_evaluate.py          # X-1 (R0-R5)
.evalenv/bin/python experiments/d1_expansion_attack.py  # D-1 re-attack
python3 -m unittest discover -s tests -t .    # 40 Gate 1 tests
```
`.evalenv` carries numpy, used only by the LSA experiment. The compiler itself
still has zero third-party runtime dependencies.
