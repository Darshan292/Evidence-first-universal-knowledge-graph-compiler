# ABSTENTION / SUPPORT GATE — DESIGN

**Status:** Design. Written before implementation. **Date:** 2026-09-22

## 0. Correction to a Gate 1.5 statement

Gate 1.5 said *"a ranking retriever cannot abstain."* That is too blunt and is
withdrawn. The accurate statement:

> **A raw top-k ranking stage does not by itself express "no supported result".
> A separate support decision is therefore required.** The ranker proposes
> candidates; a distinct gate decides whether any candidate is supported well
> enough to expose as an evidence-backed result.

Ranking and support are different questions with different metrics. A
top-ranked document is *a candidate*, not *an answer*.

## 1. Architecture

```
query
  → candidate retrieval        (R0/R1/R2 … ranking; unchanged)
  → support evaluation         (this gate; deterministic)
  → ABSTAIN | EXPOSE | EXPOSE_CONFLICTED
  → [answer generation — not authorized, not built]
```

Retrieval correctness (Recall@k, MRR) and support correctness (false-support,
false-abstention) are measured separately and never merged into one number.

### Why three outcomes, not two

A binary gate forces a wrong answer on one real case. Consider:

> Corpus: `config.py` says 30 s; `runbook.md` says 60 s and self-describes as
> stale. Query: *"confirm the current gateway deadline is 60 seconds."*

"60 seconds" **is** present in the corpus, verbatim, in a real document. Abstaining
denies known information; exposing it silently asserts a contradicted value as
fact. The correct behaviour is to expose **both sides with the conflict marked** —
which this project already models (`claim_relation.CONTRADICTS`). So the gate
returns:

| Outcome | Meaning |
|---|---|
| `ABSTAIN` | no candidate is sufficiently supported |
| `EXPOSE` | at least one candidate passes every necessary condition |
| `EXPOSE_CONFLICTED` | passes, but the supporting artifacts disagree; both are returned |

## 2. Shape of the decision: conjunction, not a score

**The gate is a conjunction of independent necessary conditions, not a weighted
formula.** Reasons:

1. A weighted score lets a strong signal mask a hard failure — exactly how a
   query asking for an unsupported number gets exposed because its wording
   matched well.
2. Every abstention must be explainable by naming the condition that failed.
   "Score 0.43 < 0.51" explains nothing.
3. One calibrated parameter is auditable; five interacting weights are not.

A candidate is **supported** only if it passes **all** applicable conditions.
The gate exposes the best-ranked supported candidate, or abstains.

## 3. Candidate signals, assessed individually

Each is assessed on: what it measures · why it should correlate with support ·
failure mode · deterministic? · needs calibration?

### S1 — Verifiable evidence span **[ADOPTED, mandatory]**
- **Measures:** the candidate resolves to a locator whose `quoted_text`
  re-derives byte-exactly from the artifact at the pinned hash.
- **Why:** this project's entire premise. A result without localized evidence is
  not a supported result by definition.
- **Failure mode:** none for support (it is necessary, not sufficient) —
  a fabricated span simply cannot exist, because Gate 1 enforces this at the
  database level.
- **Deterministic:** yes. **Calibration:** none.

### S2 — Distinctive-term anchoring **[ADOPTED, calibrated]**
- **Measures:** whether at least one *rare* query term (high IDF over the corpus)
  appears in the candidate's evidence span. Rare terms carry the query's intent;
  common ones do not.
- **Why:** "order" appearing in a Kafka question is not support. "PAYMENT_TIMEOUT_SECONDS"
  appearing is.
- **Failure mode:** a true paraphrase whose distinctive terms are all synonyms of
  the source's — the class that caused the Gate 1.5 paraphrase failure. This will
  cause false abstentions on hard paraphrases, and that cost is accepted and
  measured rather than hidden.
- **Deterministic:** yes (IDF computed from the frozen corpus).
- **Calibration:** yes — the IDF percentile that counts as "distinctive". Chosen
  on CALIBRATION only.

### S3 — Literal constraint satisfaction **[ADOPTED, the key condition]**
- **Measures:** every numeric or quoted literal in the query must appear in the
  evidence span.
- **Why:** this is what defeats the hard-negative class. *"why is the payment
  gateway deadline 90 seconds"* shares almost all its vocabulary with the true
  source, so every lexical signal passes it. But `90` does not appear in the
  evidence, and the requested fact is therefore unsupported.
- **Failure mode:** a query whose literal is legitimately expressed differently
  in the source ("half a minute" vs "30"). Measured, not assumed away.
- **Deterministic:** yes. **Calibration:** none — it is exact presence.

### S4 — Graph relationship grounding **[ADOPTED, conditional]**
- **Measures:** when a query names two corpus symbols and a relationship, the
  corresponding edge must exist in the compiled graph.
- **Why:** defeats "wrong relationship" negatives — *"which function in ledger.py
  calls CardGateway.authorize"* — where the entities are real and the vocabulary
  matches, but the edge does not exist. Nothing lexical can catch this; the graph
  can, deterministically.
- **Failure mode:** only applies when both endpoints resolve to corpus symbols;
  silent on prose queries. Restricted to `DERIVED` edges, so a heuristic edge
  never grants support.
- **Deterministic:** yes. **Calibration:** none.

### S5 — Contradiction state **[ADOPTED, routes outcome, does not gate]**
- **Measures:** whether supporting artifacts disagree.
- **Why:** selects `EXPOSE_CONFLICTED` rather than silently picking a side.
- **Deterministic:** yes (reads `claim_relation`). **Calibration:** none.

### Signals considered and REJECTED

| Signal | Why rejected |
|---|---|
| **Raw retrieval score threshold** | BM25 scores are corpus- and query-length dependent; not comparable across queries. Any threshold would be fitted to this corpus, not to support. |
| **Score margin (top1 − top2)** | Measures ranking confidence, not evidential support. A query with one obviously-best *wrong* answer has a large margin. Tempting and misleading. |
| **Raw query-term coverage** | This is the Gate 1.5 `grounded()` rule that produced false-support = 1.0. Superseded by S2, which weights by distinctiveness. Explicitly not reused. |
| **Number of independent supporting artifacts** | Correlates with topic popularity, not truth. The stale runbook would gain support from being one of several documents mentioning timeouts. |
| **Cross-mechanism agreement (R0∩R1)** | Two lexical methods agreeing on a lexically-overlapping wrong answer is not evidence. Would fail exactly the hard-negative class. |
| **Any semantic entailment heuristic** | Would be inventing proof. Prohibited by the gate's own terms. |

## 4. The support test, stated precisely

For a query `q` and candidate `c` with evidence span `e`:

```
supported(q, c) :=
      S1: e re-derives byte-exactly from its artifact at the pinned hash
  AND S2: at least one query term with IDF ≥ θ appears in e
  AND S3: every numeric/quoted literal in q appears in e
  AND S4: if q names two corpus symbols and a relation, a DERIVED edge exists

decision(q) :=
   ABSTAIN            if no candidate is supported
   EXPOSE_CONFLICTED  if supported and its artifacts carry a CONTRADICTS relation
   EXPOSE             otherwise
```

**θ is the only calibrated parameter**, chosen on CALIBRATION data alone.

## 5. What this design deliberately cannot do

- It cannot recognise a true paraphrase whose every distinctive term is a synonym.
  S2 will abstain. That is a **false abstention**, it is the accepted cost of a
  strict gate, and it is measured and reported rather than tuned away.
- It cannot verify that evidence *entails* the answer, only that the requested
  literals and distinctive terms are present. Entailment is not deterministic and
  is not attempted.
- It is not a probability. No calibrated confidence is claimed, because no
  probability calibration is performed.

## 6. Validity requirement

A gate that abstains on everything is trivially safe and useless. The design is
therefore evaluated on **both** axes, and a high false-abstention rate is a
failure of the gate, not a conservative success.
