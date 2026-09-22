# IMPLEMENTATION PLAN

**Status:** Planning. **Date:** 2026-09-22.
**Nothing below is implemented.** Phase 0 output is this document set only.

---

## 1. The smallest vertical slice that proves the architecture

One command must carry a corpus end-to-end through **every architectural layer**.
Breadth is deliberately sacrificed; if any layer is skipped, the architecture is
unproven.

```
kgc compile ./eval/corpus --out kg.sqlite --deterministic-only
kgc query  kg.sqlite "where is the payment timeout defined and what uses it?"
kgc ui     kg.sqlite
```

The slice is proven when this holds:

> `PAYMENT_TIMEOUT_SECONDS` in `config.py` is extracted deterministically with a
> byte range → a function reading it produces a `READS` edge → a design document
> discussing "payment timeout" produces an `EntityMention` with page+bbox → the
> two resolve to one `CanonicalEntity` with a recorded score and reason → a
> second document contradicting the value produces a `CONTRADICTS` edge with both
> claims retained → a query returns all of it → the UI renders the neighbourhood
> → clicking any node opens the source at the exact byte range or page region.

That single path exercises: router, two adapter families, the IR, deterministic
extraction, evidence anchors, candidate generation, entity resolution, claims,
conflict handling, storage, hybrid retrieval, visualization and click-through.
Everything else in the project is an extension of a layer this path already
proves.

## 2. Module layout

```
kgc/
  cli.py              # compile | query | ui
  router.py           # modality detection, hashing, path/archive safety
  ir.py               # IR records + locator constructors (validating)
  store.py            # THE ONLY module containing SQL (ADR-0004)
  extract/
    python_ast.py     # stdlib ast + symtable → CodeSymbol, imports, calls
    pdf.py            # pdfplumber → Region + pdf_box locators
    docx.py           # python-docx → Region + docx_para/docx_cell locators
  candidates.py       # rule-based entity/relation candidates (no model)
  resolve.py          # tiered entity resolution
  enrich/
    port.py           # complete_structured(...) + cache + ledger + budget
    ollama.py openai.py anthropic.py groq.py
    schemas/          # versioned JSON Schemas
  claims.py           # claim builder + evidence verifier (the trust boundary)
  retrieve.py         # exact | bm25 | graph expansion | ranking
  ui/                 # static HTML + vendored sigma.js; reads the SQLite file
eval/
  corpus/  gold/  run_eval.py
tests/
```

No `base.py`, no `factory.py`, no `registry.py`, no `manager.py`. If one appears,
it must first justify itself in an ADR.

## 3. Sequence

Each step ends with the repository buildable and its tests passing.

### Step 1 — Store and IR *(foundation)*
Schema from DATA_MODEL.md, including the
`claim_requires_verified_evidence` trigger. Validating locator constructors.
**Done when:** A-06 passes — a claim with a fabricated quote *cannot be inserted*.

> Built first on purpose. The central guarantee should exist before there is any
> code that might want to bypass it.

### Step 2 — Router + Python extraction
Hashing, modality detection, path/archive safety. `ast` + `symtable` →
`CodeSymbol` with byte ranges; imports; intra-module call resolution;
`UNRESOLVED` marking. `parse_status` on failure.
**Done when:** A-02, A-03 pass.

### Step 3 — Document extraction
`pdfplumber` → regions with page+bbox; `python-docx` → paragraph/run/table
locators. Heading hierarchy. Sentence segmentation preserving offsets.
**Done when:** A-04, A-05 pass (including: no DOCX row carries a page number).

### Step 4 — Graph projection + retrieval
`node`/`edge` projection; FTS5 index; exact lookup, BM25, recursive-CTE
expansion; query-class router; evidence ranking.
**Done when:** A-15 passes per class.

### Step 5 — Candidates + entity resolution
Rule-based candidates; tiered resolution writing `mention_resolution` rows with
score and reason; deferral instead of guessing.
**Done when:** A-08 passes (false-merge rate ≤ 0.01).

### Step 6 — Visualization
Static UI, vendored sigma.js, reads the SQLite file. Neighbourhood
materialisation with depth/node caps, filters, evidence inspector, source preview
with byte/page/paragraph jump, contradiction view.
**Done when:** A-16, A-17 pass.

> **Milestone 1a ends here — and it is fully useful with zero LLM calls.**
> If the project stopped at this point it would still be a working,
> evidence-first knowledge graph compiler. That is the intended property.

### Step 7 — Provider port + Ollama
`complete_structured`, cache, ledger, budget, circuit breaker; Ollama adapter;
versioned schemas; the data-channel discipline.
**Done when:** A-13 passes; injection suite scaffolded.

### Step 8 — Claim builder + enrichment
Evidence verifier; structural-edge protection; rationale/intent extraction;
coreference; ambiguity adjudication.
**Done when:** A-07, A-11 pass, unsupported-claim rate = 0.

### Step 9 — Conflict, versioning, resumability
`CONTRADICTS` / `SUPERSEDES`; validity intervals; `work_item` resume; crash test.
**Done when:** A-09, A-10, A-12, A-14 pass.

### Step 10 — Cloud adapters + release gates
OpenAI, Anthropic, Groq adapters; parity tests; license gate; no-egress test.
**Done when:** A-18, A-19, A-20 pass. **Milestone 1 complete.**

## 4. Why this order

Two deliberate inversions of the obvious sequence:

1. **The evidence trigger is built in Step 1, before any extractor.** Guarantees
   added after the code they constrain get negotiated away under deadline.
2. **The LLM appears in Step 7, after the system is already useful.** This makes
   C-1 (useful with zero remote calls) structurally true rather than aspirational,
   and it means every semantic feature must justify itself against a working
   deterministic baseline instead of being assumed necessary.

## 5. Phase 2+ (not planned in detail — deliberately)

- **M2 — modalities:** image, audio, video adapters; new `locator_kind` values;
  cross-modal linking. The ADR-0002 test: this must require **no** change to
  `claim`, `evidence`, `node`, `edge`.
- **M3 — analysis depth:** tree-sitter (language #2), SCIP/pyright-backed symbol
  resolution, dense embeddings *if* the eval demands them, community summaries.
- **M4 — distribution:** packaging, reproducibility suite, benchmark release,
  security hardening, contributor tooling.

Detailed planning for these is withheld on purpose. Planning M3 now would mean
designing against measurements we do not have.

## 6. Ponytail review checkpoints

Run before declaring any step complete:

- What did we add, and could an existing module have done it?
- What could have been deleted instead of added?
- Which abstraction is genuinely load-bearing? Which is speculative?
- Which dependency could have been avoided?
- Which LLM calls could have been deterministic, cached, or batched?
- Did we preserve every accuracy, security and provenance guarantee?

## 7. Definition of done

Milestone 1 is complete when acceptance tests **A-01 … A-20** pass, the license
gate is green, and the deterministic-only path passes the full extraction and
retrieval suite with an empty `llm_ledger`. Not when a demo looks good.
