# X-1 PRE-REGISTRATION

**Committed before any retrieval code was written or any result was observed.**
Git history is the evidence: this file's commit precedes the retrieval
implementation commit. Nothing below may be changed after results are seen; a
correction must be a new version with a recorded reason.

**Date:** 2026-09-22

---

## 1. What is being decided

Whether the retrieval architecture requires **dense vectors**, **graph
traversal**, both, or neither — and what the minimum deterministic
code-analysis contract must be for the graph to serve as an evaluation
substrate.

## 2. Fixed inputs (frozen before measurement)

- **Corpus:** `eval/corpus/` — 15 files (7 Python modules, 5 documents, 3
  structured-data files).
- **Resolution corpus:** `eval/rescorpus/` — 11 Python files covering direct,
  aliased, from-, re-export, shadowed, circular, third-party and stdlib cases.
- **Gold:** `eval/EVALUATION_GOLD.json` (23 queries) and
  `eval/CODE_ANALYSIS_GOLD.json` (13 call sites, 14 import edges).
  Both authored by reading the corpora, before retrieval existed.

## 3. Separation rules (enforced, not merely stated)

| Component | May read | May NOT read |
|---|---|---|
| corpus | — | gold, retrieval, evaluator |
| gold | corpus | retrieval output |
| retrieval | corpus | **gold** |
| evaluator | corpus, gold, retrieval output | — |

The retrieval implementation must not import, open or reference any gold file.
This is asserted by a test that greps the retrieval module for gold paths.

## 4. Retrieval configurations

| ID | Configuration |
|---|---|
| R0 | exact identifier / path lookup only |
| R1 | lexical BM25 (SQLite FTS5) |
| R2 | R1 + graph traversal (bounded, ranked, per ADR-0007) |
| R3 | dense semantic retrieval only |
| R4 | R3 + R1 |
| R5 | R3 + R1 + graph |

**Query preprocessing** (stopword removal, OR semantics, identifier splitting)
is applied identically to R1, R2, R4, R5. It is not a tuned component: the
stopword list is a fixed general-purpose English list, and identifier splitting
is a mechanical camelCase/snake_case rule. **No alias table, no synonym list, no
query-specific rule.** Any such mechanism, if demonstrated, is labelled a
mechanism demonstration and excluded from the benchmark.

## 5. Dense retrieval constraint — declared in advance

**Neural sentence-embedding models cannot be obtained in this environment**
(`huggingface.co` is unreachable — measured, HTTP 000). Therefore R3 uses
**LSA (TF-IDF + truncated SVD)**: deterministic, fully local, no model download,
no API.

This is declared **before** results because it bounds the conclusion:

> LSA is a **lower bound** on dense-retrieval performance. A neural embedding
> model would plausibly do better on paraphrase. Therefore:
> - If LSA **does** clear the adoption threshold, dense retrieval is justified.
> - If LSA **does not**, the result is **inconclusive for neural models** and
>   must be reported as such — not as "dense retrieval doesn't help."

## 6. Metrics

**Retrieval:** Recall@1, Recall@5, Recall@10, MRR — computed per query class,
never reported only in aggregate.

**Evidence:** a result is *evidence-correct* only if the retrieved unit is a gold
answer unit **and** the returned span contains `evidence_must_contain` verbatim.
Reported as evidence precision, evidence recall, localization accuracy.

**Negative queries:** `false_support_rate` — fraction of unanswerable queries for
which the system returns a confident supported answer.

**Conflict:** `conflict_preservation_rate` — fraction of conflict queries where
**both** sides are returned.

**Graph:** correct entity resolution (must-not-merge pairs stay separate),
correct relationship traversal.

## 7. Decision rules — fixed in advance

Let `ΔR@10` be absolute Recall@10 improvement over the next-simpler
configuration on the named class.

**D-A — Adopt graph traversal (R2 over R1)** if and only if:
- `ΔR@10 ≥ 0.05` on `multi_hop` **or** `structural_code` **or** `cross_document`, **and**
- no class regresses by more than `0.02`, **and**
- added query latency p95 `≤ 50 ms` on this corpus.

**D-B — Adopt dense retrieval (best of R3/R4/R5 over best of R0–R2)** if and only if:
- `ΔR@10 ≥ 0.10` on `paraphrase`, **and**
- `ΔR@10 ≥ 0.05` on overall answerable queries, **and**
- index build `≤ 60 s` for this corpus, **and**
- query p95 `≤ 200 ms`, **and**
- the configuration runs fully offline with no API key.

**D-C — Disqualification (overrides everything).** Any configuration with
`false_support_rate > 0` on negative queries is **disqualified regardless of
recall**. Fabricating support is a correctness failure, not a tradeoff.

**D-D — Evidence gate.** A configuration whose evidence-correct rate is more
than `0.15` below its answer-correct rate is reported as **failing the evidence
requirement**, even if its recall is the highest. A semantically correct answer
with wrong evidence is a failure for this project.

**D-E — Simplicity tie-break.** If two configurations are within `0.03` Recall@10
of each other on every class, the simpler one wins. "Simpler" is defined in
advance as, in order: fewer components, no model download, smaller index.

## 8. Code-analysis decision rules

**D-F — Cross-module resolver.** Implement deterministic cross-module resolution
if the measured `DETERMINISTIC` recall on `eval/rescorpus/` call sites is
`< 0.80` against gold, counting only sites whose gold `should_resolve` is
`DETERMINISTIC`.

**D-G — Tree-sitter.** Adopt only if it materially improves a guarantee that
`ast` cannot provide, measured on the four categories (valid Python,
cross-module, syntax errors, unsupported language). "Recovers partial symbols
from a malformed file" counts as material **only if** the recovered symbols are
correct — measured, not assumed.

**D-H — False-positive veto.** Any resolver change that produces a single
incorrect `DETERMINISTIC` target (e.g. resolving `shadowed.run -> pkg.core.helper`
instead of the local `shadowed.helper`) is rejected regardless of recall gain. A
wrong deterministic edge is worse than an honest `UNRESOLVED`.

## 9. What would falsify the current architecture

- Lexical retrieval matching dense retrieval on paraphrase → the Phase-0
  "defer vectors" position was right and vectors stay out.
- Dense retrieval clearing D-B → ADR-0004's "no vector store" needs revisiting.
- Graph traversal not clearing D-A → the graph is not earning its place in
  retrieval, and the project's central premise needs re-examination.
- Any configuration fabricating support on negative queries → the evidence model
  is not being enforced at retrieval time.

## 10. Prohibited in this gate

No LLM anywhere: no query expansion, no reranking, no answer generation, no
alias generation, no entity resolution. Measured by the same grep test that
enforces §3.
