# ARCHITECTURE

**Status:** Planning (Phase 0). No implementation exists.
**Last verified against upstream sources:** 2026-09-22.

> Every technology claim in this document was verified on 2026-09-22 by direct
> installation, execution, registry metadata, or official source. Claims that
> could **not** be verified are explicitly marked `UNCERTAIN` and carry a named
> experiment. Nothing here is inferred from a library's marketing copy.

---

## 1. Central rule

```
DETERMINISTIC EXTRACTION FIRST
  → SEMANTIC ENRICHMENT ONLY WHERE NECESSARY
    → CLAIM + EVIDENCE MODEL
      → VALIDATION
        → GRAPH
          → HYBRID RETRIEVAL
            → VISUALIZATION
```

The LLM is a semantic enrichment component. It is **not** the source of truth
and **not** the graph constructor. A model-produced statement enters the graph
only as a `Claim` carrying `Evidence` that points at bytes in a source file.

The inverse rule is equally binding and is the one most systems get wrong:
**a deterministic fact is never overwritten by a model-produced one.** When they
disagree, both are retained and the disagreement is itself recorded.

## 2. Pipeline

```
                 ┌──────────────────────────────────────────┐
  INPUTS         │ files, directories, archives             │
                 └────────────────────┬─────────────────────┘
                                      ▼
  ROUTER         identify modality; SHA-256 every artifact; refuse unsafe paths
                                      ▼
  ADAPTERS       python_ast │ pdf_plumber │ docx  ← M1 scope
                 (image/audio/video adapters are M2; they add Region subtypes,
                  they do NOT change the IR)
                                      ▼
  CANONICAL IR   Source·Artifact·Document·Region·CodeSymbol·EntityMention
                 every object carries an EvidenceAnchor (§4)
                                      ▼
  DETERMINISTIC  imports, definitions, class/function structure, docstrings,
  EXTRACTION     assignments, call sites, headings, tables, cells
                 → status = DETERMINISTIC
                                      ▼
  CANDIDATE      lexical/rule-based entity + relation candidates
  GENERATION     (no model involved)
                                      ▼
  ENTITY         path/FQN match → normalized string → alias rules → lexical
  RESOLUTION     similarity → shared attributes → [LLM adjudication, last resort]
                 never silently merges; every decision keeps score + reason
                                      ▼
  SELECTIVE      ONLY the tasks in the matrix in PROJECT_SPEC §5 reach a model.
  ENRICHMENT     structured output → schema validation → evidence check → cache
                                      ▼
  CLAIM BUILDER  every semantic assertion becomes a Claim + ≥1 Evidence row
                 unsupported claims are REJECTED, never repaired with invented spans
                                      ▼
  VALIDATION     schema, evidence-exists, evidence-substring, contradiction scan
                                      ▼
  STORE          ONE SQLite file: nodes, edges, claims, evidence, FTS5, job state
                                      ▼
  RETRIEVAL      exact identifier → BM25 (FTS5) → graph expansion → evidence rank
                                      ▼
  UI             sigma.js/WebGL graph + evidence inspector + click-through to
                 the exact byte range / page bbox / paragraph in the source
```

## 3. Storage: one SQLite file, and why not a graph database

**Decision: SQLite is the single store of record for M1.** Full reasoning in
[ADR/0004-storage.md](ADR/0004-storage.md). Two measured facts drove it.

**Measured 2026-09-22** — synthetic graph, 50,000 nodes / 250,000 edges,
stdlib `sqlite3` with recursive CTEs, indexed `edge(src)`:

| Operation | p50 | p95 |
|---|---|---|
| 1-hop neighbourhood | 0.02 ms | 0.04 ms |
| 2-hop neighbourhood | 0.08 ms | 0.15 ms |
| 3-hop neighbourhood | 0.34 ms | 0.58 ms |
| 4-hop neighbourhood | 1.63 ms | 4.23 ms |
| exact identifier lookup | 0.063 ms | — |

A dedicated graph engine buys nothing at this scale, and it costs something
specific and severe:

> **Two stores means no atomic commit.** If the graph lives in a `.lbug` file
> and evidence lives in `.sqlite`, a crash between the two commits leaves an
> evidence row with no node, or a node whose evidence never landed. For a system
> whose entire premise is "every claim has evidence", a non-transactional seam
> between claims and their evidence is a correctness defect, not a performance
> trade-off.

One file, one transaction, one fsync. Crash recovery becomes SQLite's problem,
which is the most tested crash-recovery implementation available to us.

LadybugDB was evaluated seriously and **smoke-tested successfully** (see
ADR-0004 §3 for the transcript). It is retained as a *derived, rebuildable
projection* with an explicit, measurable adoption trigger — not as the store of
record, and never as a second source of truth.

## 4. Evidence anchors — what is actually achievable per modality

This is the part most architectures overstate. Verified empirically on
2026-09-22:

| Modality | Locator actually available | Verified how |
|---|---|---|
| Python | byte range, line:col, end line:col, docstring, qualified name | stdlib `ast` executed |
| PDF | page number, char-level bbox `(x0, top, x1, bottom)`, word bbox | `pdfplumber` on a hand-built PDF |
| DOCX | paragraph index, run index, char offset, table/row/cell | `python-docx` round-trip |

> **DOCX has no page numbers and no bounding boxes.** Page breaks in OOXML are
> computed by the renderer, not stored in the file. `python-docx` exposes no page
> attribute — confirmed by execution, not assumption. Any design promising
> "page N" evidence for DOCX is lying. Our DOCX locator is
> `(paragraph_index, run_index, char_start, char_end)`, and the UI jumps to the
> paragraph, not to a page.

Every `Evidence` row stores the **most specific locator the parser genuinely
produced**, plus the SHA-256 of the artifact and the exact `quoted_text`. A
locator is never upgraded to a precision the parser did not supply.

## 5. What gets deterministic treatment vs. a model

Summarised here; the binding table is PROJECT_SPEC §5.

**Deterministic (no model, ever):** file metadata, hashing, language ID, AST
structure, imports, definitions, docstrings, assignments, intra-file call sites,
PDF page/bbox/text, DOCX paragraph/table structure, heading hierarchy.

**Model-eligible (must produce Claim + Evidence):** rationale and intent behind a
decision, entity typing where no rule suffices, cross-document linking,
coreference, ambiguity adjudication, contradiction interpretation, query intent
classification, answer synthesis.

## 6. Provider abstraction

One narrow port, four adapters (Ollama, OpenAI, Anthropic, Groq). Ollama is the
default and requires no API key. Cloud providers are opt-in and never receive
data unless the user explicitly selects them. Every response is validated against
a JSON Schema before it is trusted; provider-native structured output is used
where it exists and is treated as an optimisation, never as a guarantee.
See [ADR/0003](ADR/0003-llm-provider-abstraction.md) and
PROVIDER_CAPABILITY_MATRIX.md.

## 7. Security posture

Ingested content is **data, never instructions**. Source text is passed to models
inside a delimited, clearly-labelled data channel, and extraction prompts state
that instructions found within the data are to be extracted as *content*, not
followed. Prompt-injection strings found in a document are a finding to record,
not a command to obey. See RISK_REGISTER R-11.

Path traversal, archive bombs (zip-slip, nested expansion), symlink escape and
resource exhaustion are handled at the router before any parser sees a byte.

## 8. Explicitly rejected for M1

| Rejected | Why | Re-open when |
|---|---|---|
| Qdrant / any vector server | SQLite FTS5 (stdlib) gives BM25 offline; a server contradicts local-first | BM25+graph misses the Recall@10 target on the semantic query class |
| Dense embeddings in M1 | Unproven need; adds model download, breaks zero-setup | same trigger as above |
| Docling as default parser | Resolves to **119 packages incl. full CUDA/torch stack** (measured) | opt-in extra for scanned/OCR/complex-layout PDFs |
| Tree-sitter in M1 | stdlib `ast` is the *normative* Python grammar | language #2, or parse-failure rate > 2% |
| Graph database as store of record | 4-hop p95 = 4.2 ms in SQLite; 2 stores = no atomic commit | traversal p95 > 200 ms or > 5M edges |
| LLM-constructed graph | the project's founding rejection | never |
| Microservices / brokers / queues / K8s | single-user local tool | not foreseeable |

## 9. Known-uncertain items

| # | Uncertainty | Experiment |
|---|---|---|
| U-1 | Ladybug FTS/vector extensions download from a vendor CDN at runtime; offline behaviour unknown | attempt install on a networked host; test vendoring the `.lbug_extension` binary |
| U-2 | Local model quality for rationale extraction at 7–8B | run the E-2 gold set against 3 local models before committing defaults |
| U-3 | BM25 + graph sufficiency without dense vectors | measure Recall@10 per query class on the eval corpus |
| U-4 | Ladybug's long-term maintenance (pre-1.0 fork, ~10 months old) | quarterly review; SQLite store of record means this is not on the critical path |
