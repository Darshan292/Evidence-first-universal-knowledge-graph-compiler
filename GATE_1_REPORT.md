# GATE 1 REPORT — Deterministic Foundation

**Date:** 2026-09-22 · **Version:** 0.1.0-gate1 · **Schema:** 1
**Verdict: CONDITIONAL PASS** (conditions in §10)

Every claim in this report is tagged:
**[MEASURED]** executed and recorded · **[DECISION]** a design choice made here ·
**[ASSUMPTION]** believed, not verified · **[UNCERTAIN]** unresolved.

No assumption is presented as evidence.

---

## 1. What was built

Authorized scope 1–13, complete. 1,040 lines of implementation, 40 acceptance
tests, zero third-party runtime dependencies — stdlib only.

```
kgc/ids.py                 content-addressed identifiers
kgc/ir.py                  canonical IR + validating locators
kgc/safety.py              ingestion boundary + hardened XML/JSON
kgc/store.py               ALL SQL; invariants as triggers + independent audit
kgc/evidence.py            typed, per-locator verification
kgc/analysis/interface.py  language-independent analysis boundary (ADR-0005)
kgc/analysis/python_backend.py   stdlib `ast` backend (the only backend)
kgc/analysis/mapper.py     CodeAnalysis -> IR
kgc/pipeline.py            transactional ingestion + crash resume
kgc/cli.py                 ingest | stats | verify | show | diagnostics
```

**Nothing outside scope was built.** No LLM code, no provider adapters, no
embeddings, no retrieval, no ranking, no UI, no Tree-sitter, no Docling, no
plugin system.

Verified by import audit — every import in `kgc/` is stdlib
(`ast`, `sqlite3`, `hashlib`, `json`, `os`, `pathlib`, `dataclasses`, `enum`,
`datetime`, `argparse`, `sys`, `xml.parsers.expat`) plus intra-package imports.
**Zero third-party runtime dependencies.**

A name-grep over `kgc/` returns exactly one match: the phrase "Tree-sitter, SCIP
or a compiler backend" in the `interface.py` docstring, explaining what the
boundary exists for. It is documentation, not an integration.

## 2. Measurements

### 2.1 Scale **[MEASURED]**

Synthetic corpora, 10 symbols/file, this container:

| Files | Source | DB | Cold index | Files/s | Warm re-index | Speedup | Audit | Reproducible | Peak RSS |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 0.00 MB | 0.48 MB | 0.03 s | 308 | 0.02 s | 1.7× | 0.001 s | yes | 20.5 MB |
| 100 | 0.05 MB | 3.44 MB | 0.35 s | 288 | 0.16 s | 2.2× | 0.006 s | yes | 24.5 MB |
| 1000 | 0.48 MB | 33.2 MB | 4.89 s | 205 | 1.75 s | 2.8× | 0.056 s | yes | 48.2 MB |

Real corpus (the compiler ingesting itself): 12 files, 0.18 s, 377 symbols,
964 claims, 2.21 MB, 0 invariant violations.

Indexing is ~linear in file count with mild degradation (308 → 205 files/s over
100×). Memory grows sub-linearly: one artifact is held at a time.

### 2.2 Crash recovery **[MEASURED]**

Crash injected at 6 code points × 4 corpus positions (10/30/50/90%), then resumed:

| Crash at | Recovered identical to uninterrupted run | Invariant violations |
|---|---|---|
| 10% | yes | none |
| 30% | yes | none |
| 50% | yes | none |
| 90% | yes | none |

Points covered: `before_insert`, `during_entity`, `during_relationship`,
`during_evidence`, `before_commit`, `after_commit`. Every one recovers to a
byte-equal row set.

### 2.3 Coverage — two different numbers that must not be conflated **[MEASURED]**

Self-ingest of `kgc/`:

**LLM involvement:** 964 claims, **100% `DERIVED`**, **0 claims with `model_id`**.
The foundation is entirely deterministic.

**Symbol resolution** — a *different* measure, and the honest one:

| Resolution | Count | Share |
|---|---|---|
| `DETERMINISTIC` | 67 | 11.2% |
| `HEURISTIC` | 196 | 32.7% |
| `UNRESOLVED` | 336 | **56.1%** |

Why 56% is unresolved, by recorded reason:

| Count | Reason |
|---|---|
| 293 | not bound in any statically known scope (`print`, `store.close`, `ap.add_argument`) |
| 41 | call target is a computed expression |
| 2 | attribute access on a module-scope value |

> **This is the Python dynamic-typing boundary, reported rather than hidden.**
> Method calls on local variables and builtins cannot be resolved without type
> inference. Every one is recorded as an `UNRESOLVED` row with its surface name
> and a reason — not silently dropped, and never guessed. `IMPORTS` is 100%
> `HEURISTIC` because the import statement is deterministically observed while
> its target lives in an artifact this backend does not cross-reference.

## 3. Defects found during implementation

Four, all found by tests or measurement rather than inspection:

| # | Defect | How found | Fix |
|---|---|---|---|
| G1-1 | Diagnostics duplicated on re-ingest (284 → 568) — no deterministic identity | idempotency test | `diagnostic_id` content-addressed |
| G1-2 | `RUNNING` state and `attempts` rolled back with the work transaction, so a crash-inducing file would retry **forever** | crash-resume test failing | work item claimed in its own committed transaction; `MAX_ATTEMPTS=3` quarantine |
| G1-3 | Invariant audit scaled superlinearly: 0.002 → 0.059 → **7.5 s** | benchmark | missing `claim_evidence(evidence_id)` index; **2.214 s → 0.003 s**, full audit **7.5 s → 0.056 s** |
| G1-4 | A crash test targeted artifact #1 = `broken.py`, which early-returns on the FAILED path — **the test was vacuous** | test failure after re-targeting | retargeted to an artifact reaching the full write sequence |

G1-4 is the one worth dwelling on: a passing test that tests nothing is worse
than a failing one.

## 4. Architecture corrections made during implementation

**[DECISION] `reference` collapsed from a parallel fact store to a detail table.**
It originally duplicated `claim` (both stored subject/predicate/object/locator).
It now holds only `(claim_id, to_name, resolution, reason)` — resolution quality
for a reference-style claim. One fact store, not two.

**[DECISION] Evidence is claim-independent and content-addressed**, linked via
`claim_evidence`. Identical evidence is stored once and shared. This also made
the write ordering natural: evidence first, then the claim citing it, so the
invariant triggers see a complete picture (ADR-0006 Part 4).

**[DECISION] Deleted in the simplification pass** (§12): the `Reference`
dataclass (superseded), `Store.pending_work` (never called), `ids.region_id`
(no producer in Gate 1), `CodeAnalysis.capability_report` (the CLI reports the
same from persisted rows, which is the real truth), and `Modality.DOCUMENT` /
`STRUCTURED` (values nothing could produce).

**Kept deliberately:** the analysis interface (a real boundary — ADR-0005), the
locator type union, the four-axis claim model, the independent invariant audit,
and `CrashPoint` (test-only, but crash-boundary tests are not optional).

## 5. Invariants, and how they are enforced

Enforced **in the database** as triggers, so they hold against any future caller
that bypasses the Python API — plus `check_invariants()` as an independent
whole-database audit.

| Invariant | Mechanism | Test |
|---|---|---|
| A claim with no evidence cannot exist | trigger | `test_claim_without_evidence_is_rejected` |
| A claim citing missing evidence cannot exist | trigger | audit |
| A trusted claim needs `EXACT`/`REPRODUCIBLE` evidence | trigger | `test_every_trusted_claim_resolves_to_valid_evidence` |
| A model may not establish a structural predicate | trigger | `test_model_cannot_establish_structural_predicate` |
| `STRUCTURAL`-only evidence cannot support `CONFIRMED` | trigger | `test_structural_only_evidence_cannot_confirm` |
| A `DERIVED` claim may not carry `model_id` | trigger | `test_derived_claim_may_not_carry_model_identity` |
| `confidence=0.92` never implies verified | no code path reads `confidence` | `test_high_confidence_does_not_imply_verified` |
| No dangling evidence | audit | idempotency + crash tests |
| An `UNRESOLVED` reference has no resolved target | audit | `test_unresolved_references_are_recorded_not_omitted` |
| A failed artifact records a reason | audit | `test_malformed_file_is_explicit_failure_not_empty_graph` |
| No read path filters on `lifecycle` | source assertion | `test_no_default_query_path_filters_on_lifecycle` |

The last one is unusual and deliberate: it reads `store.py` and fails if a read
method filters on `lifecycle`, because that is precisely how defect D-3 happened.

## 6. Identifier classification **[DECISION]**

| Class | Identifiers | Property |
|---|---|---|
| **Content-addressed** | `content_sha256`, `artifact_id` | function of bytes alone |
| **Deterministic-derived** | `source_id`, `symbol_id`, `evidence_id`, `claim_id`, `entity_id`, `diagnostic_id`, `config_hash` | function of content + producer identity; stable across runs |
| **Database-local** | *none* | no surrogate keys exist |
| **Intentionally non-deterministic** | `run_id` only | runs must be distinguishable |

`claim_id` includes `model_id`, `prompt_version` and `schema_version`, so two
models over identical bytes produce **coexisting, diffable** claims rather than
one silently overwriting the other. Verified by
`test_model_identity_participates_in_claim_id`.

**[MEASURED]** Two independent runs over the same corpus produce identical
`symbol`, `claim`, `evidence`, `reference` and `diagnostic` row sets at all three
scales. Only `run_id` and timestamps differ.

## 7. Security findings

| Vector | Result | Test |
|---|---|---|
| Path traversal `../../etc/passwd` | rejected | `test_path_traversal_rejected` |
| Absolute path outside root | rejected | `test_absolute_path_outside_root_rejected` |
| Symlink escape | refused, never followed | `test_symlink_escape_rejected` |
| Binary masquerading as text | rejected on NUL scan | `test_binary_masquerading_as_text_rejected` |
| Oversized file | rejected with recorded reason | `test_oversized_file_rejected_with_reason` |
| **XXE (`file://` entity) in the verifier** | **rejected — DOCTYPE refused** | `test_xxe_is_inert_in_evidence_verification_path` |
| Billion laughs | rejected | `test_billion_laughs_rejected` |
| Prompt injection in comments/docstrings | inert; became ordinary `DERIVED` content | `test_injection_text_is_data_not_instruction` |

**The finding worth naming:** risk R-19 was real. Python's default XML stack
resolves external entities, so a hostile document could have caused local file
reads *during evidence verification*. `parse_xml_hardened` is built directly on
expat with DOCTYPE, entity declaration and external-entity handlers all refusing.
Verified against a live `file://` payload pointing at a real secret file.

Rejections are **recorded as artifacts** with `parse_status='SKIPPED'` and a
reason — never silently skipped.

**[ASSUMPTION]** Archives are **not supported**, so zip-slip and zip-bomb defences
are **not implemented and not tested**. This is a scope decision, not a claim of
safety. Listed as blocked work (§11).

## 8. Known limitations

1. **[MEASURED] Storage is 68.8× source size** (0.24 MB source → 16.7 MB DB at
   500 files). Breakdown: indexes 35% of the file; 64-character hex IDs stored as
   TEXT account for ~5.06 MB of raw key text, which would be ~0.63 MB as 16-byte
   BLOBs. *My first hypothesis — `quoted_text` duplication — was wrong:
   `quoted_text` totals only 1.7× the source.* Measuring corrected the guess.
2. **[MEASURED] 56% of references are `UNRESOLVED`** on real code, and
   cross-module resolution is not implemented at all (all `IMPORTS` are
   `HEURISTIC`). Recorded honestly, but it limits how much the graph can answer.
3. **[MEASURED] `ast` recovers nothing from a malformed file.** One syntax error
   loses the whole file's symbols. Recorded as `FAILED` + diagnostic, never as an
   empty success — but the content is lost until a Tree-sitter backend exists.
4. **[DECISION] Single-writer.** Concurrent readers are unaffected (measured in
   the earlier review: 1,049 reads, 0 blocked). A second writer blocks.
5. **[ASSUMPTION]** Throughput figures come from synthetic 10-symbol files in one
   container. Real corpora with large files, deep nesting and heavy comments are
   unmeasured.

## 9. Answers to the ten gate questions

**1. Is the canonical IR stable enough for the next layer?** **Yes, provisionally.**
It absorbed three locator kinds and a table collapse without a schema redesign.
**[UNCERTAIN]** it has only met one modality and one language — the real test is
the first document adapter.

**2. Are structural relationships deterministic and provenance-safe?** **Yes.**
100% of claims are `DERIVED`; every one carries extractor id + version, schema
version, run id and verified evidence. A model cannot create a structural
predicate — enforced by trigger, not convention.

**3. Can every trusted fact be traced to evidence?** **Yes.** Every `DERIVED`
claim has ≥1 `EXACT` evidence row, and the test re-reads each artifact from disk,
re-hashes it, slices the byte range and compares — for all of them, not a sample.

**4. Are contradictions preserved rather than hidden?** **Yes.** `CONTRADICTS`
and `SUPERSEDED` claims remain queryable, verified by test, plus a source-level
assertion that no read path filters on `lifecycle`.

**5. Are IDs reproducible?** **Yes** — measured identical at all three scales.

**6. Is ingestion idempotent?** **Yes** — three consecutive ingests produce
identical counts. One defect (G1-1) was found and fixed proving it.

**7. Is crash recovery deterministic?** **Yes** — 6 crash points × 4 positions,
all recovering to byte-equal row sets, with a retry bound preventing infinite
re-crash.

**8. Is hostile input isolated safely?** **Yes for the implemented surface**
(paths, symlinks, binaries, oversize, XXE, injection text). **No for archives** —
not implemented, not claimed.

**9. Is deterministic-only mode genuinely useful?** **Partly, and I will not
overstate it.** It compiles a corpus, establishes symbols, containment, imports
and 67 deterministic call edges with byte-exact evidence, and answers
"what does this contain / what does it call" via `kgc show`. But with 56% of
references unresolved and no cross-module resolution, it is a *foundation*, not
yet a useful knowledge graph. Its value is that everything it does assert is
true and traceable.

**10. What architectural assumptions remain unproven?**
- The IR's modality-independence (only code has exercised it).
- That `HEURISTIC` cross-module imports can be promoted to `DETERMINISTIC` by an
  import resolver without changing the IR.
- That 68.8× storage is acceptable, or fixable without touching every module.
- Everything about retrieval — deliberately untouched, and X-1 still blocks it.

## 10. Verdict: CONDITIONAL PASS

The authorized scope is complete, all 40 tests pass, every invariant is enforced
in the database and independently audited, crash recovery is verified at six
points, and reproducibility is measured rather than asserted.

**Conditions to resolve before Step 4 (retrieval):**

| # | Condition | Why it blocks |
|---|---|---|
| C-1 | Decide on the storage representation for identifiers (measured: 68.8× source; ~8× of it recoverable via BLOB IDs) | retrieval adds indexes on top of an already index-heavy schema; changing ID representation later touches every module |
| C-2 | Implement cross-module import resolution, or accept `HEURISTIC` imports as permanent and say so | retrieval over a graph whose cross-file edges are all unresolved will under-perform for reasons unrelated to retrieval design, and would corrupt X-1's result |
| C-3 | Decide whether a Tree-sitter backend is needed now (measured: `ast` loses 100% of a malformed file's symbols) | parse-failure rate on a real corpus is still unmeasured; §8 ADR-0005 set the trigger at >2% |

**Not blocking, but named:** archive support is absent (§7), and all throughput
figures are from synthetic corpora (§8.5).

## 11. Explicitly still blocked

LLM enrichment · provider adapters (Ollama/OpenAI/Anthropic/Groq) · embeddings ·
vector stores · BM25 retrieval · graph ranking · bounded expansion (ADR-0007) ·
UI / sigma.js · multimodal · audio/video · Docling · Tree-sitter · SCIP ·
archives · distributed execution · plugin systems.

**Experiment X-1 remains blocking for Step 4** and has not been run: it requires
the evaluation corpus, which is not part of Gate 1.

## 12. Reproducing this report

```bash
python3 -m unittest discover -s tests -t .   # 40 tests
python3 bench/benchmark.py                   # measurements in §2
python3 -m kgc.cli ingest kgc --db kg.sqlite # self-ingest
python3 -m kgc.cli verify --db kg.sqlite     # independent invariant audit
python3 -m kgc.cli show   --db kg.sqlite evidence.verify
```
