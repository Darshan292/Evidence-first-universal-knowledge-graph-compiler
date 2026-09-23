# ARCHITECTURE DECISION — Evidence-first semantic pivot after J-1

**Date:** 2026-09-23 · **Status:** decision document, no implementation
**Decision: CONTINUE — the evidence-first semantic pivot is justified, with a sharpened thesis (§13).**

Every claim in this document is argued from J-1 measurements or from measurements
taken while writing it. Where I am reasoning rather than measuring, I say so.

---

## 1. What J-1 actually falsified

**1.1 "A rule-based query IR can serve independent users."** Falsified.
Full parse rate 0.190 on answerable questions. Worse than the rate: **six of the
nine `PARSED` queries had bound the subject to the literal word "What"**. The IR
does not merely under-cover; it reports success on sentences it has entirely
misread. Nine hand-written relation patterns and three property patterns met 54
natural developer sentences and understood three.

**1.2 "The deterministic structural graph contains what developers ask about."**
Falsified. An emitted predicate suffices for **1 of 42** answerable questions
fully and 8 partially. 33 of 42 require information no predicate expresses.

**1.3 "Parser coverage is the bottleneck."** Falsified as a complete account.
The parser is the *first* blocker for 40 of 42 and the *binding* constraint for
9. Fixing it perfectly answers 9 questions. Fixing only the claim layer answers
0, because nothing reaches it. Both are necessary; neither is sufficient; the
deeper one is the claim layer.

**1.4 "0.000 false support demonstrates that the trust boundary works."**
Falsified as evidence. The system exposed **zero** answerable queries. A system
that always abstains scores perfectly on every safety metric ever devised. Six
gates of safety results were measured on a system that never answered anything.
**This is the single most important thing in this document and §12 returns to it.**

**1.5 "The vocabulary gap is a matter of adding the declared-but-unemitted
predicates."** Falsified. `HAS_DEFAULT`, `HAS_TYPE`, `HAS_PURPOSE` and `DEFINES`
together address the 6 parameter-default questions and part of the 4
docstring-prose ones. The largest group — **17 of 42, 40%** — asks what the code
*does and when*, which no predicate of that family expresses.

## 2. What J-1 did NOT falsify

**2.1 The deterministic compiler is correct.** 140/140 artifacts compiled, 0
absent, 0 failed, invariants clean, two independently built databases
byte-identical, definition-claim precision 1.000 on 400 sampled, `EXTENDS`
precision 1.000 on 150, evidence spans 400/400 byte-exact. Nothing in J-1
contradicted any of it.

**2.2 The evidence model.** Every claim carries a byte-exact, independently
re-verifiable span. J-1's harness re-read the original corpus files rather than
trusting the database, and found no discrepancy — on zero exposed queries, so
this is "not falsified", not "confirmed".

**2.3 Occurrence identity.** K-1.1's rule — a qualified name is a name, identity
is a source occurrence — held at 6,102 symbols with 493 duplicated names. **0 of
42 failures had an identity cause.**

**2.4 Retrieval.** J-1 recorded 0 retrieval failures. That is not a
pass — retrieval was never reached. So I measured it directly while writing
this:

| retrieval layer (no model, no embeddings) | reaches the gold evidence |
|---|---|
| exact identifier → symbol → file | 24/42 (57%) |
| BM25 over raw corpus files, top-5 | **38/42 (90%)** |
| BM25 over raw corpus files, top-10 | **42/42 (100%)** |
| BM25 over function/method/class spans, top-10 | 30/42 (71%), median 4.4K tokens |

**Retrieval is not the problem and does not need new infrastructure.** SQLite in
the Python standard library already has FTS5 (verified: sqlite 3.45.1). §7.

**2.5 Abstention as a first-class outcome.** 12 of 12 must-not-answer questions
were refused, and the four ambiguous ones were refused *as* ambiguous where the
parser got far enough to test it. The machinery is intact; it is untested under
load.

## 3. What is preserved unchanged

These survive the pivot without modification. They are the six gates' actual
product.

| preserved | why J-1 supports keeping it |
|---|---|
| the deterministic Python compiler (`kgc/analysis`, `kgc/pipeline`) | precision 1.000, 100% artifact coverage, byte-identical reruns |
| content-addressed identity; occurrence identity for parents and reference subjects | 0 identity failures across 6,102 symbols, 493 duplicate names |
| the evidence store: byte range + sha-pinned artifact + re-derivable quotation | independently re-verified in the J-1 harness against original files |
| database-enforced invariants (claim needs evidence, trusted claim needs verified evidence, structural predicate needs derivation, literal claim carries its value) | none fired; they are the cheapest correctness guarantee in the system |
| the four orthogonal axes — lifecycle, **establishment**, verification strength, epistemic state | §9 shows `establishment` is exactly the mechanism the semantic layer needs |
| abstention as a named outcome with a failed-invariant reason | the only reason J-1 produced no wrong answers |
| `ParseStatus` as a *concept* — a query that is not fully understood must not silently become another query | correct idea; §6 replaces the implementation, not the rule |
| single-source policy (`kgc/predicates.is_trusted`, `artifact_identity`) | K-1.2's regression tests depend on it |
| determinism and fresh-database discipline | the reason J-1's two runs are comparable at all |

## 4. Assumptions that must be retired

**4.1 "No LLM in any role."** Retired. This was a discipline, not an
architectural necessity, and J-1 shows it is now the binding constraint on
usefulness. §2 of the brief is right: it is artificial. The trust boundary it was
protecting is a *separate mechanism* (§7, §8, §9) and survives without it.

**4.2 "PARSED means the query was understood."** Retired. It means the fields
are filled. §6.

**4.3 "Structural facts are the product."** Retired. They are the substrate and
the falsifier (§13). J-1: 1 of 42 questions answerable from them.

**4.4 "Growing the predicate vocabulary grows usefulness proportionally."**
Retired. The relationship is not proportional and it saturates: the entire
declared vocabulary addresses ≤9 of 42.

**4.5 "A claim's evidence is one span."** Retired for semantic claims. "How does
`safe_join` prevent traversal" is supported by a function body *and* its
docstring *and* possibly its caller. The evidence model must admit an evidence
**set**; the per-span exactness guarantee is unchanged.

**4.6 "Only `.py` matters."** Retired. 4 of 42 answerable questions are about
`pyproject.toml` and `CHANGES.rst`; 85 of 225 artifacts are never read. Those
files are already recorded as `UNSUPPORTED` artifacts — the honesty mechanism
works, the coverage does not.

**4.7 "Safety is established."** Retired, emphatically. §12.

## 5. The smallest architecture that answers J-1's questions

Two stages, and the first is not new.

### Stage A — deterministic enrichment (no model)

The substrate already holds more than it exposes. Measured:

* **721 symbols carry a docstring** (569 under `src/`), **231,780 characters**,
  captured at ingestion, stored on `symbol.docstring`, and surfaced by **zero
  predicates**.
* Function/method spans are already addressable: `safe_join` is bytes 4684–7215
  of `security.py` with a 1,455-character docstring; `run_simple` is 6,332 bytes
  with 3,810 characters of docstring.

Stage A emits what the AST can establish cheaply and the vocabulary already
names or nearly names: parameter names and defaults (`HAS_DEFAULT` — 6 questions),
annotations (`HAS_TYPE`), module-level literals and `ast.AnnAssign` (completing
`HAS_VALUE` — K-1 deferred both, at measured cost in `J1-006`), raised
exceptions, environment-variable reads, decorators (the 283 edges K-1.2 correctly
removed from `CALLS` have no home yet), and the docstring as an addressable
evidence span rather than an unused column.

Stage A is deterministic, cheap, offline, and it closes **at most 9–13 of 42**.
It is worth doing and it is not the pivot.

### Stage B — semantic interpretation at query time

For the 17 behavioural questions and the prose questions, the required fact is
not a triple. It is an explanation supported by source. The minimum machinery:

```
question
  → interpreted intent            (LLM-assisted, UNTRUSTED — §6)
  → deterministic retrieval        (identifier + FTS5/BM25 + graph traversal — §7)
  → candidate evidence set         (exact spans from the existing evidence store)
  → semantic reasoning             (LLM, constrained to the retrieved spans)
  → candidate answer + citations   (each sentence cites evidence ids)
  → deterministic grounding gate   (§8 — re-reads bytes, checks citations)
  → structural falsification       (§13 — the graph contradicts the answer?)
  → ANSWER (labelled PROPOSED) or ABSTENTION
```

**No file is sent to a model at ingestion time.** Query-time only, on retrieved
spans. This is what keeps the cost model viable (§10) and it is reversible: a
batch enrichment pass can be added later if measurement justifies it.

## 6. Where an LLM may participate

| role | allowed | why it is safe |
|---|---|---|
| **query interpretation** — sentence → structured constraints | **yes** | its output is a *hypothesis*, validated against the compiled graph before anything is exposed. A wrong interpretation produces an abstention or a failed lookup, not a wrong fact |
| **semantic interpretation of retrieved source** | **yes** | it reads spans the deterministic layer selected; it cannot choose its own evidence |
| **behavioural explanation** | **yes** | with mandatory citation and the §8 gate |
| **multi-claim synthesis** | **yes** | the claims it synthesises are already evidence-bound |
| **semantic claim proposal** | **yes**, entering at `establishment = PROPOSED` | §9: the existing trust boundary already refuses to treat `PROPOSED` as a trusted answer |
| **documentation/prose understanding** | **yes** | docstrings are already stored with exact spans |

## 7. Where an LLM must never be authoritative

| forbidden | enforcement that already exists |
|---|---|
| inventing `CALLS`/`IMPORTS`/`EXTENDS`/`CONTAINS` facts | the `structural_predicate_requires_derivation` trigger **aborts the insert** — a model-established structural claim is unrepresentable in the database, not merely discouraged |
| resolving ambiguous identity | `OccurrenceIndex` refuses; `decide()` returns `ABSTAIN_AMBIGUOUS`. A model may *suggest* a disambiguator; the deterministic layer must confirm it against source occurrence |
| manufacturing evidence | `claim_requires_evidence` and `claim_evidence_must_exist` triggers; evidence ids are content-addressed and cannot be minted |
| declaring anything verified | `trusted_claim_needs_verified_evidence`; verification strength is set by a verifier that re-reads bytes, never by a producer |
| writing or altering a locator | locators come from the AST. **A model never emits a byte offset.** It cites an evidence id that already exists |
| judging whether the bytes support its own answer | §8. This needs an independent check and the honest limits of that check are stated there |

**The trust boundary is not weakened by admitting an LLM**, because the boundary
was never "no model exists" — it is "a model-produced assertion cannot acquire
`DERIVED` establishment, cannot carry unverified evidence, and cannot be exposed
as a trusted answer". All three are enforced in the database and in
`is_trusted`, and all three are untouched by this pivot.

## 8. How semantic answers stay evidence-bound

**The contract.** Every sentence of an exposed answer cites at least one
`evidence_id`. Before exposure, a deterministic gate that contains no model:

1. **Existence** — every cited id is a real row.
2. **Provenance** — every cited id was in the retrieved set for *this* question.
   A citation the retriever never offered is fabrication, and the answer is
   rejected outright, not repaired.
3. **Byte exactness** — the artifact is re-read from disk and the stored
   quotation re-derived. This is the check K-1.1's harness already performs.
4. **Quotation integrity** — any span the answer quotes inline must occur
   verbatim in the cited bytes.
5. **Coverage** — a sentence with no citation is removed. If removal empties the
   answer, the system abstains.
6. **Structural falsification** — §13.

**What this gate cannot do, stated plainly.** It verifies *grounding*, not
*entailment*. It can prove the cited bytes exist, were retrieved, and say what
the answer says they say. It **cannot prove those bytes actually support the
claim**. An answer that cites the right function and describes it wrongly passes
every check above.

Anyone who tells you otherwise is selling something. This is the central
unsolved problem of the pivot, and the only honest mitigations are: (a) the
structural falsifier of §13, which catches the subclass of errors the graph can
contradict; (b) showing the evidence to the user so a human can check in one
glance — which is a product feature, not a guarantee; and (c) measuring
entailment with human or independent-model adjudication in J-2, not asserting it.
**Demo 0.1 must not claim verified semantic answers.** It claims *grounded,
auditable, falsifiable-where-possible* answers.

## 9. Representing semantic observations without faking determinism

**No new store. No parallel schema. The existing model already does this**, which
is the strongest evidence that Gate 1's four-axis design was right.

| axis | value for a semantic observation |
|---|---|
| `establishment` | **`PROPOSED`** — "model-produced, evidence-verified, not derivable". Defined in `kgc/ir.py` since Gate 1, never used |
| `lifecycle` | `CANDIDATE` → `VERIFIED` if an independent check confirms it → `ACTIVE` |
| `verification_strength` | of the **evidence**, not the assertion. A docstring span is `EXACT`; the interpretation of it is not evidence at all |
| `epistemic_state` / `claim_relation` | `CONTRADICTS` when the graph falsifies it (§13) |
| `predicate` | a *small* semantic set, not one per question — see below |

`kgc/predicates.is_trusted` already returns `False` for
`(HAS_PURPOSE, PROPOSED)`, and K-1.2 added a test named
`test_storable_is_not_the_same_question_as_answerable` specifically to stop a
future reader collapsing "may be stored" into "may be answered with". **That test
was written for exactly this moment.**

**The smallest semantic predicate set**, derived from the six example questions
in the brief rather than invented:

| question | semantic predicate |
|---|---|
| How does `safe_join` prevent traversal? | `HAS_BEHAVIOUR` (subject → a described behaviour, evidence-bound) |
| When does an upload spill to disk? | `HAS_BEHAVIOUR` (condition-shaped) |
| How does `LocalProxy` resolve its target? | `HAS_BEHAVIOUR` |
| What happens when a required argument is missing? | `HAS_BEHAVIOUR` (error path) |
| Why is `UserAgent` deprecated? | `HAS_PURPOSE` (already declared) |
| How does `run_simple` wire server and reloader? | `HAS_BEHAVIOUR` + existing `CALLS` |

**Two predicates, both already in or adjacent to the vocabulary.** Six questions,
one new predicate. That is the test the brief set — "do not create one new
predicate per question" — and it passes. `HAS_BEHAVIOUR` is `FUNCTIONAL`-adjacent
but should be `MULTI_VALUED`: a function has many behaviours, and Gate 2.5's
lesson is that treating a multi-valued predicate as functional manufactures false
contradictions.

**The exposure rule that keeps the two kinds separate:** a `DERIVED` claim and a
`PROPOSED` claim may both appear in an answer, but they are **never merged into
one sentence and never presented with the same status**. The UI and the API
distinguish them by `establishment`, which is already a column.

## 10. Three alternatives, compared on J-1 evidence

Not ranked. Each row cites the measurement it rests on.

| | **A. Deterministic expansion** | **B. Evidence-first semantic layer** | **C. Generic GraphRAG** |
|---|---|---|---|
| **Developer-question coverage** | ≤9/42 with a perfect parser; ≤13/42 with all Stage-A enrichment. The 17 behavioural questions are unreachable in principle | plausibly 25–35/42: retrieval reaches 42/42 at top-10, so the ceiling is set by interpretation and grounding, not by retrieval. **Unmeasured — this is the hypothesis J-2 must test** | high raw coverage; ungrounded. No ceiling claim is meaningful without a grounding measurement |
| **Safety** | perfect and vacuous — 0 exposures, 0 false support | grounding gate + structural falsification + abstention. **Unproven and must be re-measured (§12)** | citation-shaped, not citation-verified. Known failure mode: confident synthesis from adjacent-but-wrong context |
| **Evidence fidelity** | byte-exact, independently re-verified, 400/400 | byte-exact **preserved unchanged** — §8 reuses the same verifier | embeddings return chunks, not spans; provenance is a chunk id, and chunk boundaries are not semantic boundaries |
| **Implementation complexity** | low per predicate, unbounded in total — a treadmill with no terminus | moderate: query interpreter, FTS5 index, grounding gate, `PROPOSED` exposure path. **No new datastore** | high: embedding pipeline, vector store, re-embedding on change, chunking policy, a second retrieval semantics to reason about |
| **Cost** | zero | bounded: **median 4.4K tokens** of retrieved span per question, one call, cacheable by content hash | embedding the whole corpus (~309K tokens here) plus re-embedding on every change, plus query-time calls |
| **Local / offline** | complete | complete for retrieval and the gate; the model is the only network dependency, and a local model satisfies the contract because the gate is deterministic | usually requires an embedding service and a vector database; local is possible but heavier |
| **Open-source viability** | stdlib only, trivially installable | stdlib + one HTTP call to a configurable endpoint. Installable by anyone | more moving parts, more version drift, harder to reproduce a result |
| **Differentiation** | **none** — a slower `grep` with a worse query language. J-1's answerable questions are largely served by `grep` today | real but narrower than it sounds — §11 | none; this is the commodity |
| **Long-term extensibility** | a second language multiplies the predicate work | the seam is `analyze() → CodeAnalysis`; a new language adds a backend, and the semantic layer is language-independent because it reasons over spans | extends easily and degrades in provenance as it does |

**What the comparison actually shows.** A is not a strategy — it is a treadmill
whose terminus is measured at ≤13 of 42. C is available to everyone and gives up
the one thing six gates bought. B is the only option that spends the existing
substrate rather than abandoning it, and its central claim is **unproven**.

## 11. Differentiation, challenged aggressively

The thesis under test: *compiler-style normalization + source-occurrence identity
+ evidence-bound claims + a deterministic structural truth boundary + semantic
interpretation behind it + auditable answer paths + local operation.*

**The attack.** Strip the vocabulary and it reads: "RAG with citations over a
code index, plus a symbol graph." Citations are table stakes. Every serious RAG
product shows sources. The §8 gate checks that a citation exists, was retrieved
and is byte-exact — **that is roughly what good RAG citation already does.** If
this is the differentiator, it is thin, and I will not pretend otherwise.

**What survives the attack.** One thing, and it is not retrieval:

> **The deterministic graph can falsify the semantic layer.**

When a model says "`send_from_directory` validates the path via `safe_join`", the
graph either contains `send_from_directory CALLS safe_join` or it does not.
Here it does. When a model says "`X` extends `Y`" and no `EXTENDS` claim exists,
that is a detectable contradiction — not low confidence, a **contradiction**,
recordable as `claim_relation(CONTRADICTS)`, which the schema has carried since
Gate 1 and which Gate 2.5 gave correct cardinality semantics.

No RAG system can do this, because no RAG system has a deterministic ground
truth to contradict. A knowledge-graph builder cannot do it either, because its
edges are themselves model-generated — that is the "LLM-generated triples"
failure this project rejected in Phase 0 and has now spent six gates earning the
right to reject.

**The honest scope of that differentiator.** It catches structural assertions
only: calls, imports, inheritance, containment, class-body values. It cannot
falsify "the reloader prefers watchdog", because no deterministic claim covers
it. So the falsifier applies to a **minority** of the sentences in a behavioural
answer — and that minority is exactly where a model is most likely to be
confidently wrong, because invented call relationships are a known failure mode.

**Verdict on differentiation: sufficient, but narrower than the brief's framing.**
The differentiator is *structural falsification of semantic output*, not
"evidence-bound answers". If the project describes itself as the latter it is
making a commodity claim. If it describes itself as the former it is making a
claim nobody else can make. **Adopt the narrower, truer framing.**

## 12. Safety must be re-established, not inherited

**The 0.000 false-support result is not evidence that the semantic architecture
is safe, and must never be cited as though it were.**

It was measured on a system that exposed **0 of 42** answerable queries. Zero
exposures makes every safety ratio's denominator empty. The correct reading of
J-1's safety result is: *the abstention path does not crash or leak on 54 natural
questions.* Nothing more.

Every safety property has to be re-measured after the system can answer:

| property | how it must be re-tested |
|---|---|
| false support | a fresh independent query set with unsupported/ambiguous/out-of-scope items, run against a system that **does** expose answers |
| fabricated citation | adversarial: does any exposed sentence cite an id the retriever never returned? Gate: must be 0 |
| unsupported entailment | the §8 gap. Independent adjudication of whether cited bytes support the sentence. **This is the number that decides whether the pivot worked** |
| structural contradiction | how often does the graph falsify a model sentence, and is the system right when it does |
| ambiguity | does the semantic layer quietly pick one `Response` out of six |

The burned-split discipline applies: the J-1 set is now **seen** and cannot serve
as a held-out test of a system built in response to it. J-2 needs a fresh,
independently authored set.

## 13. Decision

> **CONTINUE — the evidence-first semantic pivot is justified.**

Argued from J-1, not from preference:

1. **The substrate is sound and was not falsified.** Precision 1.000,
   determinism, complete coverage, clean invariants, zero identity failures,
   zero evidence failures. Abandoning it would discard the only part of the
   system J-1 validated.
2. **The user-facing layer was falsified completely.** 0 of 42. There is no
   version of "keep going as before" that is honest.
3. **The blocking constraint is representational, not infrastructural.**
   Retrieval reaches 42/42 at top-10 with stdlib BM25. No vector database has an
   empirical trigger. The gap is that 33 of 42 required facts have no
   representation and 40 of 42 questions have no interpretation.
4. **The prohibition that caused this is artificial.** "No LLM in any role" was
   a discipline for building a trustworthy substrate. The substrate is built. The
   trust boundary that mattered — structural facts are parser-derived, evidence
   is byte-exact and independently checkable, `PROPOSED` is never a trusted
   answer — is enforced by database triggers and survives the change untouched.
5. **The existing model already expresses the pivot.** `PROPOSED` establishment,
   evidence sets, `claim_relation(CONTRADICTS)`, `is_trusted` — all present, all
   tested, none used. The pivot switches on machinery that six gates built and
   never exercised.
6. **One new predicate covers all six example questions.** The vocabulary does
   not explode.

**Adopt the narrower thesis** (§11): the product is *deterministic structural
truth used to ground and falsify semantic answers*, not *a knowledge graph with
citations*. If the falsification mechanism turns out not to catch real errors at
a useful rate, the differentiation collapses and the honest move then is
**PIVOT AGAIN**, not a louder claim.

**What would make me reverse this**, stated now so it cannot be rationalised
later: if J-2 measures entailment (§12) and finds that grounded-but-wrong answers
occur at a rate a user would notice, then the deterministic gate is not doing the
work the thesis assigns it, and "evidence-first" is decoration.

## 14. Demo 0.1 — the smallest useful vertical slice

**One repository, one question type, end to end, with the evidence visible.**

```
werkzeug → compile (exists) → ask a natural question → interpret (LLM, untrusted)
  → retrieve spans (identifier + FTS5) → answer with citations (LLM, constrained)
  → deterministic grounding gate → show answer + exact source + graph edges
```

**Target set, chosen from J-1 and defensible:** the behavioural questions about a
single named function — `J1-009` (safe_join), `J1-037` (LocalProxy), `J1-049`
(run_simple), `J1-054` (MapAdapter.build), `J1-047` (send_from_directory), plus
the Stage-A value questions `J1-006`, `J1-022`, `J1-002`, `J1-043`, `J1-038`.
**Ten of 54.** All ten have their spans and docstrings already addressable; four
are the brief's own §4 examples.

**Demo 0.1 succeeds if:** those ten produce grounded answers whose citations pass
the §8 gate; the system abstains on the 12 must-not-answer questions; every
answer displays the exact source bytes it rests on; and at least one answer shows
a deterministic `CALLS` edge beside the prose — `send_from_directory → safe_join`
is real in the graph today and is the single best demonstration of the thesis.

**Demo 0.1 explicitly does not:** answer all 42, use embeddings, send the corpus
to a model at ingestion, claim verified semantic answers, or report a safety
number.

**Cost envelope, measured:** median 4.4K tokens of retrieved context per
question, one interpretation call and one answer call, cached by the content hash
of (question, retrieved evidence ids). Viable on a free tier and on a local
model, because the deterministic gate does not care which model produced the
sentence.

## 15. Explicitly rejected

No microservices. No Kubernetes. No distributed graph. No agent swarms. No
autonomous coding agents. No ontology. No predicate-per-question. No vector
database without an empirical trigger — and §2.4 shows the trigger is absent. No
model-generated structural edges: the database physically refuses them. No
multimodal expansion before the code workflow works.

**STOP.** No implementation until this is reviewed.
