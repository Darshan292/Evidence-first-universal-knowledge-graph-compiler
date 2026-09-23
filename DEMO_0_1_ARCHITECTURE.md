# DEMO 0.1 — ARCHITECTURE

The compiler (`kgc/`) is unchanged except for one field. Everything new is in
`kgq/`, and the split *is* the trust boundary: nothing in `kgq/` may write a
claim, mint an evidence id, or invent a locator.

```
repository
   │
   │  kgc/  — deterministic compiler (unchanged)
   ▼
SQLite: artifacts · symbols · claims · evidence · diagnostics
   │
   │  kgq/  — query side (new)
   ▼
question
   ├─ interpret      LLM-assisted, UNTRUSTED, degrades to identifier extraction
   ├─ retrieve       identifier → graph hop → FTS5/BM25      (no model)
   ├─ evidence set   existing evidence ids only              (no model)
   ├─ reason         LLM, constrained to those spans
   ├─ validate       deterministic gate                      (no model)
   └─ ANSWER (PROPOSED)  or  ABSTAIN
```

## Modules

| file | lines | responsibility |
|---|---|---|
| `kgq/provider.py` | ~170 | one OpenAI-compatible HTTP shape, a content-addressed cache, and a `ScriptedProvider` for offline tests |
| `kgq/retrieval.py` | ~250 | three deterministic retrieval layers; returns existing evidence ids |
| `kgq/contract.py` | ~200 | the structured output contract, its parser, and the prompts |
| `kgq/validate.py` | ~230 | the deterministic gate and the structural check |
| `kgq/answer.py` | ~180 | the bounded answer loop |
| `kgq/cli.py` | ~230 | `compile` / `ask`, text + JSON + HTML rendering |

One change in `kgc/`: `PredicateSpec` gained `completeness` and
`completeness_scope`, because the epistemic rule in §3 needs them.

## 1. Retrieval — three layers, no embeddings

Ordered by precision. Measured on J-1 before being built
(`ARCHITECTURE_PIVOT_AFTER_J1.md` §2.4): exact identifiers reach the gold
evidence file for 57% of answerable questions, BM25 over corpus files for 90% at
top-5 and 100% at top-10. That is why there is no vector store.

1. **Exact identifier.** A name in the question that is a compiled symbol. Its
   evidence is the `CONTAINS` claim naming it, whose span is the whole
   definition. A one-line span is kept only for a variable or constant, where the
   assignment *is* the answer.
2. **Graph expansion.** One hop along **resolved** `CALLS` edges. This is what
   having a compiler buys: the answer to *"how does send_from_directory prevent
   unsafe paths"* is in `safe_join`, which the question never names and which no
   lexical search ranks highly. Unresolved targets are skipped — `os.path.join`
   has no body here, and inventing one is the fabrication everything else exists
   to prevent.
3. **FTS5 / BM25** over 1,559 function, method and class spans plus their
   docstrings. It runs when the first two layers leave fewer than two spans
   *after the budget is applied* — this ordering matters, see §7.

A span that does not fit the budget is **skipped, never truncated**: the model
must see exactly the bytes it will cite.

## 2. The model output contract

```json
{"answer": "...",
 "claims": [{"text": "...", "evidence_ids": ["<id given to the model>"],
             "quote": "<optional verbatim fragment>",
             "structural_dependencies": [
                {"predicate": "CALLS", "subject": "a.b", "object": "c.d"}]}]}
```

The model never emits a byte offset, an artifact id, a symbol id or a claim id.
A `structural_dependency` is an **assertion to be checked**, never an edge that
gets stored — and it could not be stored anyway: the
`structural_predicate_requires_derivation` trigger aborts the insert of any
structural claim that is not `DERIVED`.

## 3. The epistemic rule — absence is not contradiction

`kgc/predicates.py` now carries, per predicate:

| predicate | completeness | absence means |
|---|---|---|
| `EXTENDS` | **CLOSED_WORLD** — the direct bases in a class header, artifact parsed OK | the base is **not** there |
| `CONTAINS` | **CLOSED_WORLD** — the direct children of a symbol, artifact parsed OK | the child is **not** there |
| `CALLS` | OPEN_WORLD | **nothing** |
| `IMPORTS` | OPEN_WORLD | **nothing** |
| `HAS_VALUE` | OPEN_WORLD | **nothing** |

Four outcomes, never collapsed:

| outcome | when |
|---|---|
| `SUPPORTED` | a `DERIVED` claim affirms the assertion |
| `EXPLICIT_CONTRADICTION` | the predicate is closed-world for that scope, the artifact parsed OK, and the graph records a different set |
| `NOT_ESTABLISHED` | **the default.** The graph says nothing either way |
| `ABSTAIN` | the answer as a whole cannot be grounded |

`CALLS` resolves 1,405 of 8,375 edges here, decorator calls are excluded by
design since K-1.2, and 85 of 225 artifacts are never analysed. A missing
`CALLS` edge is evidence about the compiler, not about the code, and the
implementation refuses to treat it as anything else.

## 4. The deterministic gate

Per cited evidence id, with no model involved:

1. **exists** — a real row, else *fabricated citation*
2. **retrieved** — was handed to the model for *this* question
3. **locator valid** — addresses a real byte range in a real file
4. **bytes match** — the file is **re-read from disk** and compared to the stored
   quotation. This catches source drift since compilation
5. **quote found** — an inline `quote` must occur verbatim in the cited bytes

Then, per statement: a material statement with no evidence mapping, or whose
every citation failed, or that carries an `EXPLICIT_CONTRADICTION`, rejects the
**whole answer**.

**What the gate does not do.** It verifies *grounding*, not *entailment*. It
cannot prove the cited bytes support the statement. An answer that cites the
right function and describes it wrongly passes every check. That is stated in
the code, in this document, and in the output, and it is a J-2 measurement.

## 5. Rejection, regeneration, abstention

A rejected candidate is regenerated **once**, with the validator's reason handed
back verbatim, then the system abstains. **A statement is never silently
dropped**: dropping one leaves a shorter answer that looks fully supported.

## 6. Budget and cost

| limit | default |
|---|---|
| interpretation attempts | 1 |
| answer attempts | 2 |
| max context | 24,000 chars |
| max spans | 6 |

Recorded per question: LLM calls, input tokens, output tokens, cache hits,
regenerations, latency, and the abstention reason. Responses are cached on the
content hash of (model, messages, temperature), so an identical input never costs
twice.

## 7. Two bugs the tests caught

**Interpretation consumed the answer budget.** The scripted-provider tests
revealed that the interpretation call ate the first reply, so regeneration never
ran. Surfaced only because the tests script each call separately.

**The budget cap silently emptied retrieval.** A single oversized class body
filled the candidate list, was dropped by the character cap, and — because the
lexical fallback counted *collected* rather than *kept* spans — suppressed the
fallback, returning nothing at all. `J1-006` retrieved zero spans until this was
fixed. The fallback is now judged on what survived the budget.

## 8. Source is data

Every retrieved span is wrapped in a delimited block marked *untrusted source
content*, and the system prompt states that text inside those blocks is data —
including a sentence that says "ignore previous instructions".

The isolation is a mitigation, not the guarantee. The guarantee is the gate: a
model that **obeys** an injection and cites a fabricated id is rejected without
the validator knowing anything about injection. A fixture whose docstring
instructs the model to cite `00000000...` and declare itself verified is in the
test suite, and the system abstains on it.

## 9. Provider

No registry. Two environment variables select a provider, because Ollama, Groq
and OpenRouter all speak the same `/chat/completions` shape:

```
KGQ_BASE_URL=http://localhost:11434/v1      KGQ_MODEL=qwen2.5-coder:7b
KGQ_BASE_URL=https://api.groq.com/openai/v1 KGQ_MODEL=...  KGQ_API_KEY=...
KGQ_BASE_URL=https://openrouter.ai/api/v1   KGQ_MODEL=...  KGQ_API_KEY=...
```

stdlib `urllib` only. With nothing configured, `ask` still compiles, retrieves,
shows structural facts, and abstains from the semantic step.

## 10. What is deliberately absent

No vector database, no embeddings, no second language, no graph database, no
agents, no microservices, no ontology, no new predicate. `HAS_BEHAVIOUR` was
proposed in the first draft of the pivot and **rejected**: a semantic answer is a
query-time observation with provenance, not a permanent graph edge.
