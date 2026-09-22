# ARCHITECTURE CHALLENGE REPORT

**Phase 0 adversarial review.** Date: 2026-09-22.
**Verdict: Phase 0 does NOT pass as written. Three defects require correction
before implementation authorization.**

This review was conducted to break the architecture, not to defend it. Where the
design survived, the supporting measurement is given. Where it failed, the
failure is stated plainly and the correction is specified.

---

## 0. Executive verdict

### Defects found — these break the Phase 0 design

| # | Defect | Severity | Correction |
|---|---|---|---|
| **D-1** | **Unbounded neighbourhood expansion is semantically broken.** 3-hop undirected expansion from an *ordinary* node at 1M/5M scale returns **50,197 nodes in 184 ms**. Fast, and useless. Phase 0 specified depth/node caps but never a *ranking* contract, so "expand the neighbourhood" had no defined meaning. | **Critical** | ADR-0007: bounded, ranked, typed expansion as the only expansion primitive |
| **D-2** | **BM25 retrieval returned ZERO hits on 3 of 5 query classes.** FTS5 `MATCH` defaults to implicit AND; a paraphrased query shares no full term set with its answer. My "defer dense vectors" position rested on an untested assumption. | **Critical** | U-3 promoted to a **blocking** pre-implementation experiment; query preprocessing specified |
| **D-3** | **Phase 0's LLM rule was a crude edge-type blacklist**, and its claim model conflated *verification status* with *confidence*. The evidence verifier (byte-substring) does not generalise beyond text. | **High** | ADR-0006: verification-based rule, split lifecycle, per-type verification strategies |

### What survived, with evidence

| Claim | Attack | Result |
|---|---|---|
| SQLite is sufficient as store of record | 1M nodes / 5M edges; hub nodes; reverse traversal; concurrent readers under sustained write load | **Survived.** 252 MB; 4-hop p95 5.7 ms; 1,049 concurrent reads, **0 blocked**, max read latency 5.7 ms during heavy writes |
| Deterministic-first economics | cost model at 10 → 10,000 files | **Survived.** 53× fewer calls; $528 → $7.35 at 10k files; $0 on Ollama |
| Evidence trigger prevents unsupported claims | attempted insert without verified evidence | **Survived**, but only for text evidence (see D-3) |
| Single-writer constraint | second concurrent writer | **Confirmed limitation**, not a defect — matches ADR-0001 by design |

### The most important finding

> **D-1 is engine-independent.** A graph database would return the same 50,197
> nodes, merely faster. The Phase 0 report used "4-hop p95 = 4.2 ms" to justify
> SQLite — that measurement was taken on a uniform random graph whose 4-hop
> neighbourhood was ~300 nodes. At realistic scale with hub nodes, the problem is
> not traversal speed. **It is that an unbounded neighbourhood is not an answer.**
> No storage decision fixes this. Retrieval must rank and bound before it returns.

---

## 1. Code analysis boundary — CHALLENGE UPHELD

**The challenge is correct and Phase 0 was wrong to leave this implicit.**

Phase 0 deferred tree-sitter and used stdlib `ast`. That decision stands on its
own merits, but Phase 0 never wrote down the interface, which meant the only
concrete "code model" in the design was the one `ast` happens to produce. That is
exactly how a Python-shaped representation becomes canonical by accident.

**Correction: the Code Analysis Interface is defined now (ADR-0005), implemented
for one language.** Defining an interface is not premature generalisation;
*implementing* six backends would be. The interface is derived from what multiple
analysis technologies can express, not from what `ast` returns.

### Backend evaluation

| Technology | Resolution class it can honestly produce | Cost | Verdict |
|---|---|---|---|
| Python `ast` + `symtable` | DETERMINISTIC (structure, imports, docstrings, intra-module binding); HEURISTIC (cross-module); never types | stdlib, zero deps | **M1 Python backend** |
| Tree-sitter | DETERMINISTIC syntax only; error-tolerant; **no** name resolution, **no** types | 2 MIT wheels, no transitive deps | **M2**, when language #2 lands |
| SCIP (`scip-python`, built on Pyright) | DETERMINISTIC references and type-informed resolution | Node toolchain, per-language indexer, build step | **M3**, gated on measured cross-module recall gap |
| Compiler/LSP APIs (`tsc`, `javac`, `gopls`, `rust-analyzer`) | Strongest resolution; requires a *buildable* project | heavy; build environment per language | **M3+**, opt-in only |
| Static analysis (taint/dataflow) | HEURISTIC by nature | heavy | **Not planned**; would be claims, not structure |

The three-value resolution class — `DETERMINISTIC` / `HEURISTIC` / `UNRESOLVED` —
is now a **required field on every reference the interface emits**. A backend that
cannot resolve a reference must emit `UNRESOLVED`. It may not omit it, and it may
not guess. See ADR-0005 for the full interface and the language-capability matrix
(which languages can honestly supply `Type`, `Inheritance`, `API`, `Database`).

**Explicitly retained from Phase 0:** not every language supplies every relation.
The IR permits absence; it never fabricates. A Go backend that cannot produce
`Table` edges produces none, and the coverage report says so.

## 2. LLM semantic boundary — CHALLENGE UPHELD, rule replaced

Phase 0's rule was: *"models may not create structural edge kinds."* That is a
blacklist, and blacklists fail at their edges. It could not answer: may a model
assert `CONFIGURED_BY`? `READS`? A relation a future backend might resolve?

**The correct rule is about verification, not about edge names:**

> **A claim may enter the graph as a structural fact if and only if an independent
> deterministic analysis establishes it. A model may propose any claim; a model's
> proposal alone never establishes a structural fact.**

Formally, every claim carries an `establishment` value:

| `establishment` | Meaning | Who may produce it |
|---|---|---|
| `DERIVED` | produced by a deterministic analyser; reproducible | analyser only |
| `CONFIRMED` | proposed by a model, **independently re-derived** by an analyser | model + analyser agreement |
| `PROPOSED` | proposed by a model, evidence-verified, **not** independently derivable | model |
| `DISPUTED` | model proposal contradicts a deterministic result | recorded, never resolved silently |

The predicate vocabulary is partitioned:

- **Structural predicates** (`CALLS`, `IMPORTS`, `DEFINES`, `CONTAINS`, `EXTENDS`,
  `IMPLEMENTS`, `READS`, `WRITES`) may only appear on `DERIVED` or `CONFIRMED`
  claims. A model-only claim with a structural predicate is stored as `PROPOSED`
  **with the predicate rewritten** to its semantic counterpart, never silently
  dropped.
- **Semantic predicates** (`SEMANTICALLY_RELATED_TO`, `DESCRIBES`, `MOTIVATES`,
  `SUPERSEDES`, `MENTIONS`, `RATIONALE_FOR`) may be `PROPOSED`.

The worked example from the challenge, resolved:

```
LLM output: BillingWorkflow --CALLS--> PaymentService
  → structural predicate, establishment would be PROPOSED
  → REWRITTEN to: BillingWorkflow --SEMANTICALLY_RELATED_TO--> PaymentService
                  establishment = PROPOSED, evidence = the text span
  → IF code analysis later derives a real call edge:
       a separate DERIVED claim is created, and the PROPOSED claim is linked
       to it as SUPPORTS. The model claim is never promoted in place.
  → IF code analysis derives that no such call exists: establishment = DISPUTED.
```

This is strictly stronger than Phase 0's blacklist: it is total (covers every
predicate), it preserves the model's contribution instead of discarding it, and
it makes model/analyser agreement a first-class, queryable signal.

## 3. Provenance of provenance — CHALLENGE PARTIALLY UPHELD

Phase 0 had `processing_run`, but under-specified it and put mutable
model/provider facts in the wrong place.

**Correction — the split is by mutability and cardinality:**

**Immutable run manifest** (one row per run, hashed, never updated):
`run_id`, `started_at`, `finished_at`, `software_version`, `config_hash`,
`config_json` (the full effective configuration, stored verbatim), `status`.

**Per-claim columns** (because they vary *within* a run — a single run may use
two models, or re-run one extractor at a new version):
`extractor_id`, `extractor_version`, `model_provider`, `model_id`,
`prompt_version`, `schema_version`, `run_id`.

**Per-artifact:** `sha256` (content), `parser_id`, `parser_version`, `parse_status`.

Placing model identity on the *claim* rather than the run is the decisive detail.
Putting it on the run would make "the same corpus processed twice with different
models" distinguishable only at run granularity, and would make a mixed-model run
unrepresentable. With it on the claim, two runs over identical bytes with
different models produce **different claim IDs** (see §7) that coexist, are
queryable side by side, and can be diffed. That is the challenge's actual
requirement, and Phase 0 would have failed it.

## 4. Evidence model generalization — CHALLENGE UPHELD

Phase 0's verifier was byte-substring matching. It is correct for text and
**cannot** be the universal model — the challenge is right.

The correction introduces an explicit, honest distinction that Phase 0 lacked:

> **`verification_strength` is a property of the evidence type, not a boast.**
> The system must never mark evidence `VERIFIED` when it can only mark it
> `ASSERTED`.

| Evidence type | Locator | Verification strategy | Strength achievable |
|---|---|---|---|
| Code | file, sha256, byte range, line range, AST node path | re-read bytes at offset; exact compare; re-parse and confirm AST node kind | `EXACT` |
| PDF text | page, char span, bbox | re-extract page; exact text compare; bbox within page mediabox | `EXACT` |
| PDF region (no text layer) | page, bbox | bbox bounds-check only — **content not verifiable** | `STRUCTURAL` |
| DOCX | paragraph idx, run idx, char span / table,row,cell | re-open; index bounds; exact text compare | `EXACT` |
| CSV | row, column, sha256 | re-read row/col; exact compare | `EXACT` |
| JSON | RFC 6901 JSON Pointer | resolve pointer; exact value compare | `EXACT` |
| XML | XPath (restricted, non-evaluating subset) | resolve path; exact node text compare | `EXACT` |
| Image | bbox + OCR span | bbox bounds-check; **OCR text re-verifiable only if OCR is deterministic and pinned** | `STRUCTURAL`, or `REPRODUCIBLE` with a pinned engine |
| Audio | start/end ms, speaker | timestamp within duration; **transcript text NOT re-verifiable** | `STRUCTURAL` |
| Video | timestamp/frame, scene, bbox | frame exists; timestamp in range | `STRUCTURAL` |

Three strengths, and the claim trigger keys off them:

- `EXACT` — the quoted content was re-derived from the artifact and compared byte-for-byte.
- `REPRODUCIBLE` — re-derivable only by re-running a pinned, deterministic model (OCR/ASR engine + version + settings recorded). Re-verification is a batch operation, not an insert-time one.
- `STRUCTURAL` — the *locator* is valid (in-bounds, resolvable) but the *content* cannot be independently re-derived.

**Rule:** a claim whose only evidence is `STRUCTURAL` may **never** reach
`establishment = CONFIRMED`, and is surfaced in the UI with a distinct marker. An
ASR transcript line is evidence that *something was said at 04:12*; it is not
proof of *what* was said. Phase 0 would have silently labelled it "verified".

## 5. Claim lifecycle — CHALLENGE UPHELD

Phase 0 mixed three orthogonal things into one `status` column. The challenge is
correct: `confidence = 0.92` must never imply `verified = true`.

**Correction: four independent axes, never collapsed.**

| Axis | Values | Owned by | Meaning |
|---|---|---|---|
| `lifecycle` | `CANDIDATE → VALIDATING → VERIFIED → ACTIVE`, plus terminal `REJECTED` | the **claim** | how far through the pipeline |
| `establishment` | `DERIVED`, `CONFIRMED`, `PROPOSED`, `DISPUTED` | the **claim** | who established it (§2) |
| `verification_strength` | `EXACT`, `REPRODUCIBLE`, `STRUCTURAL` | the **evidence** | how well it can be checked (§4) |
| `epistemic_state` | `ACTIVE`, `CONTRADICTED`, `SUPERSEDED`, `UNRESOLVED` | the **relation between claims** | its standing against other claims |

`confidence` is a nullable REAL that means only "the producer's self-reported
score". It is `NULL` for `DERIVED` claims — a parser result is not a probability —
and it **participates in no gate**. Nothing in the system reads `confidence` to
decide trust.

Lifecycle transitions:

```
CANDIDATE   -- extractor emitted it, nothing checked
   ↓ schema validation passes
VALIDATING  -- structurally valid, evidence not yet checked
   ↓ every evidence row resolved and compared
VERIFIED    -- evidence check passed at its achievable strength
   ↓ contradiction scan complete, entity refs resolved
ACTIVE      -- queryable
```
Any failure → `REJECTED` with a recorded reason. `CONTRADICTED` / `SUPERSEDED`
are **not** lifecycle states: a contradicted claim is still `ACTIVE` and still
queryable. Phase 0 had these as mutually exclusive `status` values, which would
have made a contradicted claim disappear from queries. That is a real bug this
review caught.

## 6. Entity resolution failure — CHALLENGE UPHELD

The adversarial set, adjudicated:

| Pair | Verdict | Mechanism |
|---|---|---|
| A `Payment Service` ↔ B `PaymentService` | **AUTO-MERGE** | identifier normalisation (case/space/camel split) is deterministic and lossless |
| B `PaymentService` ↔ C `payment-service` | **AUTO-MERGE** | same normal form `payment service` |
| A/B/C ↔ F `payment-svc` | **DEFER — never auto** | abbreviation expansion is a *guess*. Requires corroboration: co-occurrence in one artifact, or a config/manifest mapping the names |
| A/B/C ↔ E `Payment Service v2` | **NEVER MERGE — `VERSION_OF`** | a version suffix is a *distinguishing* token. Merging destroys the temporal model (challenge §5) |
| A/B/C ↔ D `Billing Service` | **NEVER MERGE** | different head noun. Semantic relatedness is a `PROPOSED` claim, not identity |
| D `Billing Service` ↔ anything via LLM | **PROPOSED only** | a model may never produce `SAME_AS` at `establishment` above `PROPOSED` |

**The governing rule Phase 0 lacked:** normalisation that is **lossless and
reversible** (case, separators, camel-case splitting) may auto-merge.
Normalisation that **discards or invents information** (abbreviation expansion,
stemming, edit distance, embedding similarity) may only propose.

Every `mention_resolution` row carries: `method`, `algorithm_version`, `score`,
`reason`, `decision`, `run_id`, and evidence references. Reversal is a new row,
never an update — so a false merge discovered in month six is diagnosable and
undoable without re-indexing.

**Version suffixes are a trap worth naming:** `v2`, `2.0`, `-next`, `_old`.
Lexical similarity scores these as near-identical; they are the exact cases where
merging is most destructive. A dedicated version-token detector runs *before*
similarity scoring and routes to `VERSION_OF`.

## 7. Determinism / reproducibility — CHALLENGE UPHELD

Phase 0 never specified ID generation. Left unspecified, it would have become
`uuid4()`, which silently destroys reproducibility.

**Correction: content-addressed deterministic IDs.**

```
source_id   = sha256(canonical_uri)
artifact_id = sha256(source_id ‖ rel_path ‖ content_sha256)
symbol_id   = sha256(artifact_id ‖ qualified_name ‖ byte_start ‖ byte_end ‖ kind)
region_id   = sha256(document_id ‖ locator_kind ‖ canonical_json(locator))
evidence_id = sha256(artifact_id ‖ content_sha256 ‖ locator_kind ‖ canonical_json(locator) ‖ quoted_text)
entity_id   = sha256(entity_type ‖ normal_form)        -- NOT run-dependent
claim_id    = sha256(predicate ‖ subject_id ‖ object_id|object_literal ‖
                     extractor_id ‖ extractor_version ‖
                     model_id|"" ‖ prompt_version|"" ‖ schema_version|"" ‖
                     sorted(evidence_ids))
run_id      = sha256(software_version ‖ config_hash ‖ started_at)   -- deliberately unique
```

`canonical_json` = sorted keys, no whitespace, fixed float formatting.

**Consequences, which are the actual point:**

- Two runs, same corpus/config/model/versions → **identical IDs for every row
  except `run_id`**. Re-indexing is idempotent by construction.
- Same corpus, **different model** → different `claim_id` (model_id is in the
  hash). Both claims coexist and are diffable. This is precisely challenge §3's
  requirement.
- Same corpus, **different parser version** → different `claim_id`. Parser
  upgrades are visible as new claims, not silent mutations.

| Must be byte-identical across identical runs | May legitimately differ |
|---|---|
| all IDs except `run_id` | `run_id`, timestamps, wall-clock durations |
| the full node/edge/claim/evidence row set | row *insertion order* (compare as sets) |
| FTS5 index contents | SQLite physical page layout, file size |
| coverage report counts | log interleaving under parallelism |
| `llm_ledger` cache keys | ledger latency values |

Model outputs are **not** reproducible across provider-side model updates. This is
why `model_id` is in the claim hash and why the deterministic layer must stand
alone. A cloud provider silently updating a model behind a stable alias produces
new claims rather than corrupting old ones.

**Acceptance test A-12 is strengthened:** compile twice, assert the row sets are
identical after excluding the documented difference list. Phase 0's "identical
row sets" was untestable as written.

## 8. SQLite architecture challenge — SURVIVED, with one correction

Attacked on every axis named in the challenge. Measurements 2026-09-22, stdlib
`sqlite3` 3.45.1, WAL, `synchronous=NORMAL`, indexed `edge(src)` and `edge(dst)`,
power-law graph with 0.1% hub nodes absorbing 35% of in-edges.

| Axis | Measurement | Verdict |
|---|---|---|
| Database size | 1M nodes / 5M edges = **252 MB**; build 15.7 s | fine |
| Forward traversal | 4-hop p50 2.6 ms / p95 5.7 ms | fine |
| Hub forward traversal | 4-hop from top hub: 4.4 ms, 1,347 reached | fine |
| **Reverse traversal from hub** | 1-hop 5.7 ms (1,905) · 2-hop 45 ms (13,371) · **3-hop 258 ms (75,947)** | **speed fine, result unusable — see D-1** |
| **Undirected 3-hop, ordinary node** | **184 ms, 50,197 nodes** | **speed fine, result unusable — see D-1** |
| Concurrent readers under sustained write | 4 readers × 40 write batches × 5,000 rows: **1,049 reads, 0 blocked**, max read latency **5.7 ms** | **excellent** — the UI can query during indexing |
| Concurrent writers | second writer during held transaction: `database is locked` | **confirmed single-writer** — by design (ADR-0001), not a defect |
| Recursive queries | correct, but require explicit cycle handling and depth bounds | needs the ADR-0007 contract |
| Vector search | absent | see §9 |

**SQLite is NOT replaced.** The decisive evidence is the reader concurrency
result: readers are never blocked by the writer under WAL, which was the most
plausible practical objection to a single-file store powering a live UI during
indexing.

**The correction is that Phase 0 cited the wrong evidence.** "4-hop p95 = 4.2 ms"
was measured on a uniform random graph with ~300-node neighbourhoods. It did not
test hubs, reverse traversal, or undirected expansion. The honest conclusion is
narrower and stronger: *SQLite's speed was never the binding constraint;
neighbourhood semantics were, and no engine fixes that.*

### Exact replacement triggers

Replace or supplement SQLite **only** when a measured, sustained condition holds
on a real workload — not on a synthetic benchmark, not on anticipation:

| Trigger | Threshold | Action |
|---|---|---|
| Bounded-expansion p95 (after ADR-0007 ranking) | > 200 ms sustained | add graph engine as a derived projection |
| Graph size | > 20M edges **and** trigger above also met | add graph engine |
| Database file | > 50 GB | evaluate partitioning before engines |
| Writer contention | > 1 concurrent writer genuinely required | revisit ADR-0001 first — this is a scope change, not a storage change |
| Reader latency under write load | p95 > 100 ms | investigate; current measured max is 5.7 ms |
| Query expressiveness | a required query cannot be written readably as a recursive CTE **and** is used in production | add graph engine for that query class only |

Meeting **one** trigger opens an investigation. None of them auto-authorises a
migration, and a graph engine — if adopted — is a **rebuildable projection from
SQLite**, never a second source of truth.

## 9. Vector search challenge — PHASE 0 POSITION DAMAGED

This attack succeeded and the Phase 0 position must be weakened.

**Experiment.** Six-document corpus (code + prose), five query classes, gold
answers `code:config.py` + `doc:adr7`.

| Query class | Example | BM25 (FTS5 default) | BM25 + OR | + alias expansion |
|---|---|---|---|---|
| Lexical overlap | "payment timeout" | ✅ hit | ✅ | ✅ |
| Identifier | `PAYMENT_TIMEOUT_SECONDS` | ✅ hit | ✅ | ✅ |
| Paraphrase | "how long before we abandon a card charge" | ❌ **NO HITS** | ❌ | ✅ |
| Synonym only | "gateway deadline for transactions" | ❌ **NO HITS** | ❌ | ✅ |
| Conceptual | "when does billing give up" | ❌ **NO HITS** | ❌ | ✅ |
| | **gold in top-3** | **2/5** | **2/5** | **5/5** |

Three findings, in descending order of confidence:

1. **A latent bug, not an architecture problem.** FTS5 `MATCH` defaults to
   implicit AND, so a 7-word paraphrased query requires all 7 terms. This
   produced `NO HITS` — total recall failure, not poor ranking. Had this reached
   implementation unnoticed, retrieval would have looked catastrophically broken
   for reasons unrelated to the design. Query preprocessing (stopword removal, OR
   semantics, identifier splitting) is now a specified component.

2. **BM25 alone genuinely floors at 2/5.** Even with OR semantics, lexical
   retrieval cannot bridge "abandon a card charge" → `PAYMENT_TIMEOUT_SECONDS`.
   This is a real limit, not a tuning problem.

3. **The alias result (5/5) is NOT evidence and I will not present it as such.**
   I wrote that alias table while knowing the gold answers. It demonstrates that
   the *mechanism* can close the gap; it demonstrates nothing about whether
   aliases **derived automatically** from entity resolution would. Reporting 5/5
   as a result would be the exact evaluation sin this project exists to avoid.

### Correction: U-3 becomes a blocking experiment

Phase 0 said "defer dense vectors until the eval shows a gap." That was stated
as though the burden of proof lay on vectors. After this attack the honest
position is: **it is unproven either way, and it must be settled before Step 4 of
the implementation plan, not after.**

**Experiment X-1 (blocking, must complete before retrieval implementation):**
on the real eval corpus, with aliases generated *only* by the deterministic
entity-resolution pipeline (no hand curation, no knowledge of gold answers),
measure Recall@10 per query class for three configurations:

| Config | Cost if adopted |
|---|---|
| A — exact + BM25(OR, preprocessed) + graph expansion | 0 MB, 0 s, no download |
| B — A + deterministic alias expansion from entity resolution | ~0 MB, negligible |
| C — B + local ONNX embeddings (`fastembed`, Apache-2.0, no torch) | ~90–130 MB model, indexing time, storage |

**Decision rule, fixed in advance so the result cannot be rationalised:** adopt C
if and only if configuration B misses Recall@10 ≥ 0.80 on the *semantic*,
*cross-document*, or *broad synthesis* classes. Adopt B over A if B beats A by
≥ 0.05 Recall@10 on any class with no regression elsewhere.

Benchmark query classes are fixed at: `exact_lookup`, `code_symbol_lookup`,
`semantic_lookup`, `multi_hop`, `cross_document`, `cross_modal` (M2, not scored
in M1), `broad_synthesis`.

## 10. LLM economics — SURVIVED

Model assumptions (stated as assumptions, to be replaced by eval-corpus
measurement): 1,200 tokens/file; naive = 4 chunks × 2 passes/file; deterministic
-first = 15% of files need enrichment, 1 batched call each, 700 in / 350 out;
cache = 35% content redundancy.

| Files | Naive calls | Naive tokens | Det-first calls | Det-first tokens | Cached calls | Reduction |
|---|---|---|---|---|---|---|
| 10 | 80 | 72,000 | 1 | 1,050 | 0 | 80× |
| 100 | 800 | 720,000 | 15 | 15,750 | 9 | 53× |
| 1,000 | 8,000 | 7,200,000 | 150 | 157,500 | 97 | 53× |
| 10,000 | 80,000 | 72,000,000 | 1,500 | 1,575,000 | 975 | 53× |

At 10,000 files, Claude Sonnet 5 rates ($2/$10 per MTok):
**naive $528.00 · deterministic-first $7.35 · cached re-index $4.78 ·
Ollama $0.00 in all three columns.**

A second identical re-index with no source changes: **0 calls, $0.00**, cache hit
ratio 1.00.

**Zero-LLM usefulness is unchanged and remains structural**, not a mode: the
implementation plan puts the first LLM call at Step 7 of 10, after retrieval and
the UI already work (IMPLEMENTATION_PLAN §4).

## 11. Failure recovery — CHALLENGE UPHELD, gap found

Phase 0 had a `work_item` table and a resume test. Attacking the crash points
named in the challenge exposed a gap it did not cover.

**The gap: crash *between* a claim insert and its evidence insert.** Phase 0's
`claim_requires_verified_evidence` trigger fires `AFTER INSERT ON claim` — but at
that instant, within the same transaction, the evidence rows may legitimately not
exist yet. A trigger that fires too early forces evidence-before-claim ordering;
one that fires too late permits orphans.

**Correction: evidence is written first, claims second, and the check is a
deferred constraint at commit.** Concretely: write evidence rows, verify them,
then insert the claim referencing `sorted(evidence_ids)` — which the deterministic
`claim_id` hash (§7) already requires. The trigger then correctly sees the
evidence. **A crash at any point before commit leaves nothing**, because the whole
claim+evidence unit is one transaction.

Crash behaviour at each point:

| Crash at | State on disk | Resume behaviour |
|---|---|---|
| 10% (extraction) | artifacts + symbols committed per-artifact; `work_item` rows `RUNNING` | re-queue `RUNNING` → `PENDING`; re-extract; content-addressed IDs make re-insert idempotent |
| 30% (candidates/resolution) | resolution rows are append-only | re-run resolution for incomplete artifacts; identical decisions reproduce identical rows |
| 50% (enrichment) | ledger records issued calls; cache holds responses | resume from cache — **already-paid calls are not re-paid** |
| 90% (graph projection) | projection is derived | projection is rebuilt from claims; no partial-state risk |

Guarantees against the challenge's list:

- **Orphan claims** — impossible: claim and evidence share one transaction.
- **Orphan evidence** — possible *and harmless*: evidence with no claim is inert,
  and the resumed run's deterministic `evidence_id` reuses the same row. A sweep
  reports them; it does not need to delete them.
- **Unverified trusted claims** — impossible: `verified` is set before the claim
  row exists, and the trigger rejects otherwise.
- **Duplicated entities** — impossible: `entity_id = sha256(type ‖ normal_form)`
  is run-independent (§7).
- **Corrupt graph state** — the graph is a derived projection; it is rebuildable
  from claims at any time.

**A-14 is strengthened:** kill at 10/30/50/90%, resume, and assert the final row
set is identical to an uninterrupted run *and* that `llm_ledger` shows no
duplicate paid calls.

## 12. Adversarial source data — CHALLENGE UPHELD, one real hole found

Phase 0 addressed prompt injection well but treated file-level hostility as a
one-line mention. Each vector, with its control:

| Attack | Control | Where |
|---|---|---|
| PDF / code comment / README / transcript prompt injection | content only ever occupies the delimited `data_channel`; extraction calls have **no tools, no filesystem, no network**; output is a constrained object | ADR-0003 |
| Instruction that survives into the graph | becomes a claim *about* the document containing that text — true and useful | §2 |
| Zip slip (`../../etc/passwd`) | resolve every member against the extraction root; reject any path escaping it; reject absolute paths | router |
| Symlink escape | refuse symlink members entirely; never follow symlinks during walk | router |
| Zip bomb (nested / high ratio) | cap uncompressed total, per-member size, member count, and nesting depth; abort on ratio > threshold | router |
| Extremely large file | size cap before read; oversized → `artifact` row with `parse_status='SKIPPED'`, reason recorded | router |
| **Binary masquerading as text** | **null-byte + decode check before any parser** | router |
| Decompression-time resource exhaustion | stream with a hard byte budget; never `extractall()` | router |
| Malicious XML (XXE, billion laughs) | **non-evaluating parser only**: entity resolution disabled, DTDs rejected | structured adapter |

**The real hole found:** Phase 0 specified XPath as an evidence locator for XML
(§4 of the challenge requires it) without specifying the parser. Python's default
XML stack is vulnerable to entity-expansion and external-entity attacks. An
evidence-first system that ingests hostile XML with a naive parser can be made to
read local files during *evidence verification itself* — the verification path is
security-critical and Phase 0 did not treat it as such. **Correction: the XML
adapter and the XML evidence verifier both use a hardened, non-evaluating parser
with DTD and entity resolution disabled.**

**Governing principle, unchanged and now enforced at the router:** ingested
content is **data**. Nothing in the pipeline executes, evaluates, resolves,
fetches, or follows anything found inside a source artifact.

## 13. Graph explosion — CHALLENGE UPHELD (this is D-1)

Measured, 1M nodes / 5M edges, power-law:

| Expansion | Latency | Nodes returned | Renderable? |
|---|---|---|---|
| 2-hop undirected, ordinary node | 5.1 ms | 1,872 | marginal |
| **3-hop undirected, ordinary node** | **184 ms** | **50,197** | **no** |
| 2-hop undirected, hub | 99.7 ms | 23,831 | no |
| 3-hop reverse, hub | 258 ms | 75,947 | no |

Storage: 10K/50K = <1 MB · 100K/500K = 24 MB · 1M/5M = 252 MB. Storage is a
non-issue at every tested scale.

**The finding is not about rendering.** A 50,197-node result is not a
visualization problem to be solved with a cap; it is a **retrieval result with no
information content**. Truncating it to 500 arbitrary nodes would be worse —
silently and unaccountably dropping 99% of a result, which violates the project's
core commitment to not hiding what it knows.

**Correction — ADR-0007 makes bounded ranked expansion the only primitive:**

1. **Depth is not the bound; relevance is.** Expansion is a scored frontier
   (Personalised-PageRank-style / weighted BFS), returning top-K by score with
   `K` explicit, not everything within depth `d`.
2. **Edge-type filtering is mandatory at query time**, not optional in the UI.
   "What calls this?" traverses `CALLS` only.
3. **Hub damping.** A node's contribution is damped by its degree, so a
   1,905-caller utility function does not flood every neighbourhood.
4. **Truncation is always reported.** Any bounded result states the true total
   and the applied bound: *"showing top 500 of 50,197 by relevance."* Never a
   silent cut.
5. **UI expands on demand**, one frontier at a time, from a focus node.

**A-17 is strengthened:** assert that no expansion API can return an unbounded
result set, and that every truncated response carries the true total.

## 14. Architectural simplification pass

| Verdict | Item | Reason |
|---|---|---|
| **KEEP** | Single SQLite store of record | measured; readers never blocked; one transaction domain |
| **KEEP** | Evidence-first trigger + deterministic IDs | the central guarantee, now with ordering fixed (§11) |
| **KEEP** | Deterministic-first economics | 53× measured call reduction |
| **KEEP** | Four-axis claim model | §5 — collapsing them caused a real bug |
| **KEEP** | Code Analysis Interface (defined, one backend) | §1 |
| **ADD** | Bounded ranked expansion (ADR-0007) | D-1; nothing else fixes it |
| **ADD** | Query preprocessing (OR, stopwords, identifier splitting) | D-2; without it retrieval is broken for trivial reasons |
| **ADD** | `verification_strength` on evidence | §4; prevents claiming verification we cannot perform |
| **ADD** | Hardened XML parsing in adapter **and** verifier | §12; security-critical path |
| **REMOVE** | Phase 0's "models may not create structural edges" blacklist | replaced by the total, verification-based rule (§2) |
| **REMOVE** | `confidence` from every trust decision | §5; it now informs nothing that gates |
| **REMOVE** | `CONTRADICTED`/`SUPERSEDED` as lifecycle states | §5; they are relations — as states they hid claims from queries |
| **DEFER** | Dense vectors | until X-1 decides (§9) |
| **DEFER** | Graph engine | until a §8 trigger fires |
| **DEFER** | Tree-sitter | until language #2 |
| **DEFER** | SCIP / compiler backends | until measured cross-module recall gap |
| **REPLACE** | "4-hop p95 4.2 ms" as SQLite justification | replaced by the concurrency + hub measurements, which are the load-bearing evidence |
| **REPLACE** | Phase 0 `status` column | by four orthogonal axes |

**Nothing modern was added.** The three additions are a ranking function, a query
preprocessor, and a parser flag. No new runtime dependency is introduced by this
review; `fastembed` remains conditional on X-1.

## 15. Final gate — the thirteen questions

**1. What percentage of the first vertical slice can be constructed without an LLM?**

Two honest framings, because one number alone would mislead:

- **By pipeline:** Steps 1–6 of 10 (store, IR, router, code extraction, document
  extraction, graph projection, retrieval, visualization) are **100% LLM-free**.
  The system is fully useful at that point — it compiles, queries, visualizes and
  proves every evidence link.
- **By facts in the graph:** target **≥ 80%** of claims are `establishment =
  DERIVED`. For a pure code corpus this approaches 100%; for prose-heavy corpora
  it falls, because rationale and intent are genuinely not derivable.

What is **lost** without an LLM: rationale/intent claims, coreference across
paragraphs, cross-document conceptual linking, ambiguous-entity adjudication, and
natural-language answer synthesis. Everything structural, all evidence, all
retrieval and the entire UI remain.

**2. Which exact facts require semantic models?**

Only these, and only as `PROPOSED` claims with verified evidence: rationale /
intent / motivation behind a decision; entity typing where no deterministic rule
applies; coreference resolution; cross-document conceptual linking; adjudication
of ambiguity the deterministic tiers deferred; interpretation (not detection) of
contradictions; query-intent classification on ambiguous queries; answer
synthesis. Nothing else. The binding list is PROJECT_SPEC §5.

**3. What makes a claim trusted?**

Trust is not one flag, and never a score. A claim is usable when:
`lifecycle = ACTIVE` **and** `establishment ∈ {DERIVED, CONFIRMED}` **and** every
evidence row is `verification_strength ∈ {EXACT, REPRODUCIBLE}`.
A `PROPOSED` claim is *queryable and visible* but is never presented as
established fact, and `confidence` gates nothing anywhere in the system.

**4. What makes evidence verified?**

The verifier re-derived the cited content from the artifact at the stored
locator, against the pinned `artifact_sha256`, and compared it. Strength is
capped by what the modality permits (§4): `EXACT` for text/code/CSV/JSON/XML;
`REPRODUCIBLE` for pinned-engine OCR/ASR; `STRUCTURAL` when only the locator can
be bounds-checked. **The system never marks evidence verified beyond what it can
actually perform** — an ASR line is evidence that something was said at a
timestamp, not proof of what.

**5. How are conflicting claims represented?**

Both claims remain `ACTIVE` and queryable, joined by a `claim_relation` row
(`CONTRADICTS`, with a reason). No automatic winner. Deterministic claims are
never overwritten by model claims; disagreement between them is
`establishment = DISPUTED`. `SOURCE_PRIORITY` applies only when the user
explicitly configures it, and the losing claim stays queryable. Supersession sets
`valid_to` and adds `SUPERSEDES`; it deletes nothing.

**6. How are entity merges made reversible?**

A merge is an `ACCEPTED` row in `mention_resolution` carrying method,
`algorithm_version`, score, reason, evidence and `run_id`. Un-merging is a new
`REJECTED` row in a later run. Nothing is ever updated or deleted, so a false
merge found in month six is diagnosable and reversible without re-indexing. Only
lossless, reversible normalisation may auto-merge (§6).

**7. How is processing reproducible?**

Content-addressed deterministic IDs (§7). Every ID except `run_id` is a hash of
content plus the identity of everything that produced it — parser version, model
id, prompt version, schema version. Two identical runs produce identical row
sets; a changed model or parser produces *new* claims that coexist with the old
rather than mutating them. The documented may-differ list is in §7.

**8. How does crash recovery work?**

Evidence-then-claim in a single transaction, so a crash leaves nothing partial
(§11). `work_item` rows re-queue from `RUNNING` to `PENDING`. Content-addressed
IDs make re-insertion idempotent. The LLM cache means already-paid calls are not
re-paid. The graph projection is derived and rebuildable. Verified at 10 / 30 /
50 / 90% crash points by A-14.

**9. What happens when every LLM provider is unavailable?**

The compile **succeeds**. The circuit breaker opens, the job continues in
deterministic-only mode, the coverage report states which enrichment tasks were
skipped, and the ledger records why. No provider is ever on the critical path —
guaranteed structurally by the ADR-0001 boundary test: deleting the entire
semantic package must leave a system that still compiles, queries and serves.

**10. What exact measurement would force us to replace SQLite?**

Bounded-expansion p95 > 200 ms sustained on a real workload *after* ADR-0007
ranking is in place; or > 20M edges with that latency trigger also met; or a
required production query that cannot be readably expressed as a recursive CTE.
Current measurements are 5.7 ms (4-hop) and 0 blocked reads under sustained write
load, so none of these is remotely near. Any replacement is a **derived
projection**, never a second source of truth.

**11. What exact measurement would justify adding vector retrieval?**

Experiment X-1 (§9), blocking, decided in advance: adopt local ONNX embeddings
if and only if configuration B (BM25 + OR + automatically-derived alias
expansion + graph) misses **Recall@10 ≥ 0.80** on the `semantic_lookup`,
`cross_document`, or `broad_synthesis` classes on the real eval corpus, with
aliases generated with **no knowledge of the gold answers**.

**12. What exact measurement would justify adding a graph database?**

Same as Q10 — it is the same decision. Additionally, a graph engine may be
adopted for a *single query class* if that class is required in production and
its CTE form exceeds reasonable maintainability, while all other queries remain
on SQLite. Adoption is additive (a projection), never a migration of the store of
record.

**13. What exact measurement would justify distributed processing?**

None currently conceivable for the stated single-user, local-first scope, and
ADR-0001 excludes it by boundary rather than by preference. The honest
precondition: single-machine indexing throughput must first be *measured* (it has
not been), then exhausted via parallelism within one process, then via a larger
machine. Only if a real corpus cannot be indexed overnight on one machine does
the question open — and it would reopen ADR-0001's scope, not merely add
infrastructure.

---

## 16. Authorization recommendation

**Phase 0 does not pass as written.** Three defects (D-1, D-2, D-3) are real and
two of them would have produced a visibly broken system.

**Corrections are specified and recorded** in ADR-0005 (code analysis interface),
ADR-0006 (claim lifecycle, establishment, evidence verification strength) and
ADR-0007 (bounded ranked retrieval), plus the amendments in §3, §7, §11 and §12.

**One experiment blocks implementation of retrieval specifically:**

> **X-1 must complete before IMPLEMENTATION_PLAN Step 4.** It requires the eval
> corpus (Step 0) but no retrieval implementation, so it does not block Steps 1–3.

**Recommended authorization:** proceed with implementation **Steps 1–3**
(store + IR + deterministic extraction), which are unaffected by all three
defects and are the foundation every correction depends on. Hold Step 4
(retrieval) until X-1 reports. Re-review at the Step 3/4 boundary.

**What this review did not test, and should not be assumed safe:**
real-corpus parse-failure rates; actual deterministic coverage ratio on prose;
local model quality for rationale extraction (U-2); UI frame time on a real
50k-node materialisation; and the automatically-derived alias quality that X-1
exists to measure. Every number in this report from a synthetic corpus is
labelled as such, and none of them should be quoted as a property of the real
system.
