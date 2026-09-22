# PROJECT SPEC

**Status:** Planning (Phase 0). **Verified:** 2026-09-22.
> ⚠ **AMENDED 2026-09-22 by the Phase 0 adversarial review.** See
> [ARCHITECTURE_CHALLENGE.md](ARCHITECTURE_CHALLENGE.md) and ADR-0005/0006/0007. Sections marked
> **[AMENDED]** below were found defective and are superseded by that report.


## 1. What this is

An open-source, local-first compiler that turns heterogeneous source material
into a queryable knowledge graph in which **every semantic assertion is traceable
to exact bytes in an original file**.

## 2. What this is not

- Not a GraphRAG clone. The graph is *compiled* from structure, not *authored*
  by a model.
- Not a chatbot over documents.
- Not a system that requires an API key, a network connection, or a GPU to be
  useful.

## 3. The question the system must answer

> "Where is the payment timeout defined, which code uses it, which database table
> does that path touch, which document describes the design decision, and where
> was it discussed?"

and return the answer **with the evidence path** — each hop clickable back to a
byte range, page region, or paragraph.

## 4. Non-negotiable constraints

| # | Constraint | How it is enforced |
|---|---|---|
| C-1 | Useful with zero remote LLM calls | `--deterministic-only` is a first-class mode with its own acceptance tests |
| C-2 | No API key required | Ollama default; all cloud adapters opt-in |
| C-3 | No data leaves the machine unless the user names a cloud provider | egress is impossible from the deterministic path; provider selection is explicit and logged |
| C-4 | Every semantic claim carries evidence | claim insert is rejected at the DB layer without ≥1 evidence row (FK + validation) |
| C-5 | Never fabricate evidence | evidence `quoted_text` must be a verbatim substring of the artifact at the stored offsets; checked before commit |
| C-6 | Never silently merge entities | merges are rows with score, reason, evidence, and are reversible |
| C-7 | Never overwrite a contradictory claim | contradictions are recorded as `CONTRADICTS` edges; both claims stay queryable |
| C-8 | Deterministic facts outrank model facts | enforced in the claim builder; violation is a test failure |
| C-9 | Re-indexing unchanged input changes nothing | content-hash addressed; byte-identical DB assertion in tests |
| C-10 | Ingested content is data, never instructions | delimited data channel + injection corpus in the eval set |

## 5. Deterministic-vs-LLM decision matrix (binding)

`D` = deterministic, no model may be invoked.
`L` = model-eligible, **must** yield Claim + Evidence + schema validation.
`H` = hybrid: deterministic candidate generation, model only for the residual.

### 5.1 Code (Python, M1)

| Task | Mode | Mechanism | Resulting status |
|---|---|---|---|
| File metadata, SHA-256 | D | `pathlib`, `hashlib` | DETERMINISTIC |
| Language identification | D | extension + shebang | DETERMINISTIC |
| Module/class/function definitions | D | stdlib `ast` | DETERMINISTIC |
| Byte range + line:col of every symbol | D | `ast` + line-offset index | DETERMINISTIC |
| Imports (incl. `from x import y`) | D | `ast.Import`, `ast.ImportFrom` | DETERMINISTIC |
| Docstrings | D | `ast.get_docstring` | DETERMINISTIC |
| Module-level / class constants | D | `ast.Assign` with literal RHS | DETERMINISTIC |
| Call sites (syntactic) | D | `ast.Call` | DETERMINISTIC |
| Call *target resolution* within module | D | `symtable` + scope walk | DETERMINISTIC |
| Cross-module call resolution | H | import graph; unresolved → `UNRESOLVED`, never guessed | DETERMINISTIC or AMBIGUOUS |
| Dynamic dispatch, `getattr`, monkey-patching | — | **not attempted**; recorded as a known blind spot | AMBIGUOUS |
| Why a constant has its value | L | model over the symbol + docstring + nearby comments | INFERRED |

> Cross-module resolution is the honest boundary. Python is dynamically typed;
> a static resolver is necessarily incomplete. We mark what we cannot resolve
> rather than letting a model invent an edge. An `UNRESOLVED` call is a fact.
> A hallucinated `CALLS` edge is a defect.

### 5.2 Documents (PDF, DOCX — M1)

| Task | Mode | Mechanism | Status |
|---|---|---|---|
| File metadata, hash, page count | D | `pdfplumber` / `python-docx` | DETERMINISTIC |
| PDF text + char/word bbox + page | D | `pdfplumber` (verified) | DETERMINISTIC |
| DOCX paragraphs, runs, styles | D | `python-docx` (verified) | DETERMINISTIC |
| DOCX tables → row/col/cell | D | `python-docx` (verified) | DETERMINISTIC |
| Heading hierarchy → sections | D | PDF font-size clustering / DOCX style names | DETERMINISTIC |
| Sentence segmentation | D | deterministic splitter, offsets preserved | DETERMINISTIC |
| Scanned-page OCR | L | opt-in Docling extra; always probabilistic | EXTRACTED |
| Named entities in prose | H | rules/gazetteer first, model for residual | DETERMINISTIC or EXTRACTED |
| Coreference | L | model, evidence = both mention spans | INFERRED |
| Rationale / intent / decision | L | model over a bounded section | INFERRED |
| Contradiction between documents | H | rule-based detection → model interpretation | CONTRADICTED |

### 5.3 Retrieval & query

| Task | Mode |
|---|---|
| Exact identifier lookup | D |
| BM25 lexical search | D (SQLite FTS5) |
| Graph neighbourhood expansion | D (recursive CTE) |
| Evidence ranking | D (scored, explainable) |
| Query intent classification | H — rules first; model only on ambiguity |
| Answer synthesis | L — grounded strictly in retrieved evidence, with citations |

### 5.4 The rule that governs the matrix

Before any model call, in order:

1. Can deterministic analysis answer this? → then it **must**.
2. Can an existing index answer it?
3. Can a cached result answer it? (key = content hash + model + prompt version + schema version)
4. Can candidates be batched into one structured request?
5. Can ambiguity be reduced first, shrinking the context?

Only then may a request be issued, and only with the minimum evidence span
needed — never a whole repository, never a whole PDF.

## 6. Claim status model  **[AMENDED]**

> Phase 0 used a single `status` column. The adversarial review found this
> conflated four orthogonal concerns and produced a real bug: `CONTRADICTED` as a
> status value removed a claim from the `ACTIVE` set, so **a contradicted claim
> vanished from queries** — the opposite of this project's commitment. Replaced by
> four independent axes. Full specification in
> [ADR/0006](ADR/0006-claim-lifecycle-and-verification.md).

| Axis | On | Values |
|---|---|---|
| `lifecycle` | claim | `CANDIDATE` → `VALIDATING` → `VERIFIED` → `ACTIVE`; terminal `REJECTED` |
| `establishment` | claim | `DERIVED`, `CONFIRMED`, `PROPOSED`, `DISPUTED` |
| `verification_strength` | evidence | `EXACT`, `REPRODUCIBLE`, `STRUCTURAL` |
| `epistemic_state` | claim_relation | `CONTRADICTS`, `SUPERSEDES`, `SUPPORTS`, `DERIVED_FROM` |

A contradicted claim stays `ACTIVE` and queryable; its standing is a *relation*,
not a state. `confidence` is the producer's self-reported score, is `NULL` for
`DERIVED` claims, and **gates nothing anywhere in the system**.

### 6.1 The LLM boundary rule  **[AMENDED]**

Phase 0's rule — "models may not create structural edge kinds" — was a blacklist
and failed at its edges. Replaced by a total, verification-based rule:

> **A claim may stand as a structural fact if and only if an independent
> deterministic analysis establishes it.** A model may propose anything; a
> model's proposal alone never establishes a structural fact.

A model emitting `CALLS` is not discarded: the predicate is rewritten to its
semantic counterpart (`SEMANTICALLY_RELATED_TO`) at `establishment = PROPOSED`,
retaining its evidence. If an analyser later derives the real edge, a separate
`DERIVED` claim is created and linked via `SUPPORTS`. If an analyser derives that
no such edge exists, the proposal becomes `DISPUTED`. Nothing is promoted in
place.

## 7. Milestone 1 scope

**In:** Python source, PDF, DOCX → deterministic extraction → evidence anchors →
candidate generation → entity resolution → selective enrichment → claims →
SQLite graph → hybrid retrieval (exact + BM25 + graph) → sigma.js visualization →
click-through to source.

**Out:** images, audio, video, cross-modal linking, SCIP, community summaries,
dense vectors, multi-language code, plugin system, packaging for PyPI.

M2 adds modality adapters. It must not require an IR redesign — that requirement
is the acceptance test for ADR-0002.

## 8. Cost and rate control

Content-hash cache; cache key includes model id, prompt version, schema version.
Per-job call and token budget with a hard stop. Bounded exponential backoff.
Circuit breaker after repeated provider failure. A usage ledger recording every
call: provider, model, tokens, latency, cache hit/miss, and the resulting claim
ids. A reported metric: *tokens avoided by deterministic extraction*.

## 9. Definition of done for Phase 0

This document set is internally consistent, every dependency is license-audited,
every risk has a named mitigation and an owner test, and the acceptance criteria
in EVALUATION_PLAN.md are executable statements rather than aspirations.
