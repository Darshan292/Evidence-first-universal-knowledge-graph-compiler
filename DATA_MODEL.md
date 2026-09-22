# DATA MODEL

**Status:** Planning. **Verified:** 2026-09-22.
Physical model targets stdlib `sqlite3` (3.45.1 verified, FTS5 confirmed present).
> ⚠ **AMENDED 2026-09-22 by the Phase 0 adversarial review.** See
> [ARCHITECTURE_CHALLENGE.md](ARCHITECTURE_CHALLENGE.md) and ADR-0006 (lifecycle, verification strength, IDs). Sections marked
> **[AMENDED]** below were found defective and are superseded by that report.


---

## 1. Design rules

1. **Evidence is a first-class table, not a column.** A claim without an evidence
   row cannot exist.
2. **Locators are typed by what the parser actually produced.** No locator claims
   more precision than its parser supplies (DOCX has no bbox — see §4).
3. **Append-only.** Claims are never updated in place. Supersession sets
   `valid_to`; contradiction adds an edge. History survives.
4. **Reified relations.** An edge that needs provenance, confidence, temporal
   validity or qualifiers is a `claim`, not a bare row. Structural edges that
   need none stay cheap.
5. **Content-addressed.** Re-ingesting identical bytes is a no-op.

## 2. Entity-relationship overview

```
corpus 1─┬─* source 1─* artifact ──┬─* document ─* region
         │        (sha256)          └─* code_symbol
         │
         ├─* entity_mention ─* canonical_entity   (resolution is a table, not a merge)
         │
         └─* claim ─┬─* evidence ──────► region | code_symbol | artifact byte range
                    └─* edge (subject, predicate, object) when the claim is relational
```

## 3. Core tables

```sql
-- ─── provenance spine ──────────────────────────────────────────────────────
CREATE TABLE processing_run (
  run_id       TEXT PRIMARY KEY,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  tool_version TEXT NOT NULL,
  config_hash  TEXT NOT NULL,      -- full effective config, hashed
  status       TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETE','FAILED','INTERRUPTED'))
);

CREATE TABLE source (
  source_id  TEXT PRIMARY KEY,
  corpus_id  TEXT NOT NULL,
  uri        TEXT NOT NULL,        -- absolute path or URL as supplied
  kind       TEXT NOT NULL         -- 'file' | 'directory' | 'archive'
);

CREATE TABLE artifact (
  artifact_id TEXT PRIMARY KEY,
  source_id   TEXT NOT NULL REFERENCES source(source_id),
  rel_path    TEXT NOT NULL,
  sha256      TEXT NOT NULL,       -- of raw bytes; the identity of the content
  size_bytes  INTEGER NOT NULL,
  media_type  TEXT NOT NULL,
  modality    TEXT NOT NULL CHECK(modality IN ('code','document','structured','image','audio','video')),
  parse_status TEXT NOT NULL CHECK(parse_status IN ('OK','PARTIAL','FAILED')),
  parse_error  TEXT,               -- populated when PARTIAL/FAILED; never silently dropped
  UNIQUE(source_id, rel_path, sha256)
);
```

> `parse_status`/`parse_error` exist because a file we could not parse is a
> **fact to record**, not a file to skip quietly. A corpus where 8% of files
> failed to parse and nobody noticed is how evidence-first systems die.

```sql
-- ─── document structure ────────────────────────────────────────────────────
CREATE TABLE document (
  document_id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL REFERENCES artifact(artifact_id),
  title       TEXT,
  page_count  INTEGER              -- NULL for DOCX: no page geometry exists
);

CREATE TABLE region (                     -- a span of a document
  region_id    TEXT PRIMARY KEY,
  document_id  TEXT NOT NULL REFERENCES document(document_id),
  parent_id    TEXT REFERENCES region(region_id),
  kind         TEXT NOT NULL,             -- section|paragraph|heading|table|cell|figure|line
  ordinal      INTEGER NOT NULL,
  text         TEXT NOT NULL,
  locator_kind TEXT NOT NULL,             -- see §4
  locator      TEXT NOT NULL              -- JSON, shape determined by locator_kind
);

-- ─── code structure ────────────────────────────────────────────────────────
CREATE TABLE code_symbol (
  symbol_id    TEXT PRIMARY KEY,
  artifact_id  TEXT NOT NULL REFERENCES artifact(artifact_id),
  parent_id    TEXT REFERENCES code_symbol(symbol_id),
  kind         TEXT NOT NULL,             -- module|class|function|method|variable|constant
  name         TEXT NOT NULL,
  qualified_name TEXT NOT NULL,           -- pkg.mod.Class.method
  byte_start   INTEGER NOT NULL,
  byte_end     INTEGER NOT NULL,
  line_start   INTEGER NOT NULL,
  col_start    INTEGER NOT NULL,
  line_end     INTEGER NOT NULL,
  col_end      INTEGER NOT NULL,
  docstring    TEXT
);
```

## 4. Locators — the honest part

`locator_kind` determines the JSON shape of `locator`. **These are the only
shapes M1 emits, because these are the only ones the parsers verifiably produce
(tested 2026-09-22).**

| `locator_kind` | JSON | Produced by | Verified |
|---|---|---|---|
| `byte_range` | `{"byte_start":int,"byte_end":int,"line_start":int,"col_start":int,"line_end":int,"col_end":int}` | stdlib `ast` + line index | yes |
| `pdf_box` | `{"page":int,"x0":f,"top":f,"x1":f,"bottom":f,"char_start":int,"char_end":int}` | `pdfplumber` | yes |
| `docx_para` | `{"paragraph_index":int,"run_index":int|null,"char_start":int,"char_end":int}` | `python-docx` | yes |
| `docx_cell` | `{"table_index":int,"row":int,"col":int,"char_start":int,"char_end":int}` | `python-docx` | yes |

Structured-data kinds (added by the review; M1 where the adapter exists):

| `locator_kind` | JSON | Verification strength |
|---|---|---|
| `csv_cell` | `{"row":int,"col":int,"header":str}` | `EXACT` |
| `json_pointer` | `{"pointer":"/a/b/0"}` (RFC 6901) | `EXACT` |
| `xml_path` | `{"path":"/root/item[2]"}` (restricted, **non-evaluating parser**) | `EXACT` |

Reserved for M2, **not emitted in M1**: `image_box` (`STRUCTURAL`),
`audio_span` (`STRUCTURAL`), `video_span` (`STRUCTURAL`).
Adding them requires a new `locator_kind` value and a UI renderer — **no schema
migration**. That is the ADR-0002 extensibility test.

## 5. Entities and resolution

```sql
CREATE TABLE entity_mention (
  mention_id  TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL REFERENCES artifact(artifact_id),
  surface     TEXT NOT NULL,           -- literal text as it appears
  normalized  TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  locator_kind TEXT NOT NULL,
  locator      TEXT NOT NULL,
  extractor    TEXT NOT NULL,          -- 'ast' | 'rule:const' | 'llm:<model>@<promptver>'
  status       TEXT NOT NULL           -- DETERMINISTIC | EXTRACTED | INFERRED
);

CREATE TABLE canonical_entity (
  entity_id   TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  label       TEXT NOT NULL,
  created_run TEXT NOT NULL REFERENCES processing_run(run_id)
);

-- The resolution DECISION is data. Merging is never destructive.
CREATE TABLE mention_resolution (
  mention_id  TEXT NOT NULL REFERENCES entity_mention(mention_id),
  entity_id   TEXT NOT NULL REFERENCES canonical_entity(entity_id),
  method      TEXT NOT NULL,   -- fqn_match|exact_norm|alias_rule|lexical|attribute|llm_adjudicated
  score       REAL NOT NULL,
  reason      TEXT NOT NULL,   -- human-readable justification, always populated
  decision    TEXT NOT NULL CHECK(decision IN ('ACCEPTED','REJECTED','DEFERRED')),
  run_id      TEXT NOT NULL REFERENCES processing_run(run_id),
  PRIMARY KEY (mention_id, entity_id, run_id)
);
```

A "merge" is an `ACCEPTED` row. Un-merging is a new `REJECTED` row in a later
run. No row is ever rewritten, so a false merge is diagnosable and reversible
after the fact — which is the only reason false merges are survivable.

## 6. Claims, evidence, edges

```sql
CREATE TABLE claim (
  claim_id     TEXT PRIMARY KEY,
  predicate    TEXT NOT NULL,
  subject_id   TEXT NOT NULL,          -- canonical_entity | code_symbol | region
  subject_kind TEXT NOT NULL,
  object_id    TEXT,                   -- NULL for attribute-style claims
  object_kind  TEXT,
  object_literal TEXT,
  -- [AMENDED] four orthogonal axes replace the single status column (ADR-0006)
  lifecycle     TEXT NOT NULL CHECK(lifecycle IN
                  ('CANDIDATE','VALIDATING','VERIFIED','ACTIVE','REJECTED')),
  establishment TEXT NOT NULL CHECK(establishment IN
                  ('DERIVED','CONFIRMED','PROPOSED','DISPUTED')),
  rejected_reason TEXT,                -- required when lifecycle='REJECTED'
  confidence   REAL,                   -- producer's self-report. NULL for DERIVED.
                                       -- GATES NOTHING. confidence != verified.
  extractor    TEXT NOT NULL,
  model_id     TEXT,                   -- NULL unless a model produced it
  prompt_version TEXT,
  schema_version TEXT,
  valid_from   TEXT,
  valid_to     TEXT,
  run_id       TEXT NOT NULL REFERENCES processing_run(run_id),
  created_at   TEXT NOT NULL
);

CREATE TABLE evidence (
  evidence_id  TEXT PRIMARY KEY,
  claim_id     TEXT NOT NULL REFERENCES claim(claim_id) ON DELETE CASCADE,
  artifact_id  TEXT NOT NULL REFERENCES artifact(artifact_id),
  artifact_sha256 TEXT NOT NULL,       -- pinned: detects source drift
  locator_kind TEXT NOT NULL,
  locator      TEXT NOT NULL,
  quoted_text  TEXT NOT NULL,          -- MUST be verbatim at the locator
  -- [AMENDED] strength replaces the boolean: byte-compare does not generalise
  -- beyond text. Never claim verification the modality cannot support. (ADR-0006)
  verification_strength TEXT CHECK(verification_strength IN
                  ('EXACT','REPRODUCIBLE','STRUCTURAL')),   -- NULL until checked
  verifier_engine TEXT,                -- pinned engine+version for REPRODUCIBLE
  verified_at  TEXT
);

CREATE TABLE claim_relation (            -- claim-to-claim, for conflict/versioning
  from_claim TEXT NOT NULL REFERENCES claim(claim_id),
  to_claim   TEXT NOT NULL REFERENCES claim(claim_id),
  kind       TEXT NOT NULL CHECK(kind IN ('SUPPORTS','CONTRADICTS','SUPERSEDES','DERIVED_FROM')),
  reason     TEXT NOT NULL,
  PRIMARY KEY (from_claim, to_claim, kind)
);
```

### 6.1 The constraint that makes the project honest

```sql
-- [AMENDED] Evidence is written and verified BEFORE the claim, in the SAME
-- transaction. Phase 0 fired this AFTER INSERT ON claim, when evidence may
-- legitimately not exist yet -- it would have forced orphans or false rejects.
CREATE TRIGGER claim_requires_verified_evidence
AFTER INSERT ON claim
WHEN NEW.establishment IN ('PROPOSED','CONFIRMED','DISPUTED')
  AND NOT EXISTS (SELECT 1 FROM evidence
                  WHERE claim_id = NEW.claim_id
                    AND verification_strength IN ('EXACT','REPRODUCIBLE'))
BEGIN
  SELECT RAISE(ABORT, 'model-derived claim has no verifiable evidence');
END;

-- A claim whose evidence is only STRUCTURAL may never be CONFIRMED.
CREATE TRIGGER structural_evidence_cannot_confirm
AFTER INSERT ON claim
WHEN NEW.establishment = 'CONFIRMED'
  AND NOT EXISTS (SELECT 1 FROM evidence
                  WHERE claim_id = NEW.claim_id
                    AND verification_strength IN ('EXACT','REPRODUCIBLE'))
BEGIN
  SELECT RAISE(ABORT, 'CONFIRMED requires at least one EXACT/REPRODUCIBLE evidence row');
END;
```

`verified` is set only after the writer has re-read the artifact at the stored
locator and confirmed `quoted_text` matches byte-for-byte. A model that returns
a plausible quotation that does not appear in the source produces **zero** rows.
This is the single most important mechanic in the system.

## 7. Graph projection

```sql
CREATE TABLE node (
  node_id TEXT PRIMARY KEY,
  kind    TEXT NOT NULL,      -- File|Module|Class|Function|Constant|Document|Section|Table|
                              -- Concept|Person|Decision|Requirement|Claim|Evidence|Entity
  label   TEXT NOT NULL,
  ref_id  TEXT NOT NULL,      -- points into code_symbol / region / canonical_entity / claim
  ref_kind TEXT NOT NULL
);

CREATE TABLE edge (
  src TEXT NOT NULL REFERENCES node(node_id),
  dst TEXT NOT NULL REFERENCES node(node_id),
  kind TEXT NOT NULL,         -- CONTAINS|DEFINES|IMPORTS|CALLS|READS|WRITES|REFERENCES|
                              -- MENTIONS|DESCRIBES|SUPPORTS|CONTRADICTS|SUPERSEDES|
                              -- DERIVED_FROM|SAME_AS|VERSION_OF|UNRESOLVED
  claim_id TEXT REFERENCES claim(claim_id),   -- NULL iff structurally derived
  status TEXT NOT NULL
);
CREATE INDEX i_edge_src ON edge(src, kind);
CREATE INDEX i_edge_dst ON edge(dst, kind);
```

`edge.claim_id IS NULL` ⟺ the edge is a structural fact from a parser.
`edge.claim_id IS NOT NULL` ⟺ the edge is only as good as its claim's evidence.
The UI renders these differently, and retrieval can filter to structure-only.

## 8. Retrieval indexes

```sql
CREATE VIRTUAL TABLE text_index USING fts5(
  ref_id UNINDEXED, ref_kind UNINDEXED, content,
  tokenize = 'porter unicode61'
);
```

Verified 2026-09-22: FTS5 present in the stdlib build; `bm25()` ranking, phrase,
boolean and prefix queries all functional. No external dependency, no server, no
model download. Dense vectors are deliberately absent — see ARCHITECTURE §8.

## 9. Job state (resumability)

```sql
CREATE TABLE work_item (
  item_id   TEXT PRIMARY KEY,
  run_id    TEXT NOT NULL REFERENCES processing_run(run_id),
  stage     TEXT NOT NULL,
  target_id TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  state     TEXT NOT NULL CHECK(state IN ('PENDING','RUNNING','DONE','FAILED','SKIPPED')),
  attempts  INTEGER NOT NULL DEFAULT 0,
  error     TEXT
);

CREATE TABLE llm_ledger (
  call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, provider TEXT NOT NULL,
  model_id TEXT NOT NULL, prompt_version TEXT NOT NULL, schema_version TEXT NOT NULL,
  cache_key TEXT NOT NULL, cache_hit INTEGER NOT NULL,
  prompt_tokens INTEGER, completion_tokens INTEGER, latency_ms INTEGER,
  outcome TEXT NOT NULL,   -- OK|SCHEMA_FAIL|EVIDENCE_FAIL|RATE_LIMIT|TIMEOUT|REFUSED
  claim_ids TEXT           -- JSON array; links spend to what it actually produced
);
```

`llm_ledger` closes the loop between cost and value: every token is attributable
to the claims it bought, or to the fact that it bought nothing.

## 10. Extension to M2 modalities

Adding image/audio/video requires: new `modality` values, new `locator_kind`
values, new adapters. It requires **no change** to `claim`, `evidence`,
`canonical_entity`, `node`, `edge`, or the retrieval index. If a proposed M2
change forces one, ADR-0002 has failed and must be revised explicitly.


## 11. Deterministic identifiers  **[AMENDED — added by adversarial review]**

Phase 0 never specified ID generation. Unspecified, it becomes `uuid4()`, which
silently destroys reproducibility. All IDs are content-addressed:

```
source_id   = sha256(canonical_uri)
artifact_id = sha256(source_id ‖ rel_path ‖ content_sha256)
symbol_id   = sha256(artifact_id ‖ qualified_name ‖ byte_start ‖ byte_end ‖ kind)
region_id   = sha256(document_id ‖ locator_kind ‖ canonical_json(locator))
evidence_id = sha256(artifact_id ‖ content_sha256 ‖ locator_kind ‖
                     canonical_json(locator) ‖ quoted_text)
entity_id   = sha256(entity_type ‖ normal_form)          -- run-independent
claim_id    = sha256(predicate ‖ subject_id ‖ object_id|object_literal ‖
                     extractor_id ‖ extractor_version ‖
                     model_id|"" ‖ prompt_version|"" ‖ schema_version|"" ‖
                     sorted(evidence_ids))
run_id      = sha256(software_version ‖ config_hash ‖ started_at)  -- unique by design
```

`canonical_json` = sorted keys, no whitespace, fixed float formatting.

Model identity lives in the **claim** hash, not only the run. Two runs over
identical bytes with different models therefore produce **different claim IDs
that coexist and are diffable** — and a single run may legitimately use two
models. Putting model identity only on the run would make both impossible.

| Byte-identical across identical runs | May differ |
|---|---|
| every ID except `run_id` | `run_id`, timestamps, durations |
| full node/edge/claim/evidence row set | row insertion order (compare as sets) |
| FTS5 index contents | SQLite physical page layout |
| `llm_ledger` cache keys | ledger latency values |
