# K-1.1 REPORT — artifact integrity and occurrence identity

**Date:** 2026-09-23 · **Verdict: PASS — K-1.1 repaired artifact integrity and occurrence identity.**

Both defects are fixed, and the second one was worse than the seven lost files
suggested: on werkzeug, **50 reference subjects and 18 parent links were
attached to the wrong source occurrence**. The lost artifacts were the loud
symptom; those 68 silently-wrong structural edges were the quiet one, in a graph
reported at deterministic precision 1.000.

142 tests pass. All three corpora ingest with **zero absent, zero failed, zero
invariant violations**, and two-run determinism holds on every table. The seven
files are back. Historical results are preserved and diffed row by row: exactly
**2 of 132 corpus3 queries changed**, both explained below.

---

## 1. What was actually wrong

`by_qname[qualified_name] = symbol_id` kept whichever occurrence came last. A
qualified name is not unique:

```python
def outer():
    if cond:
        def worker(): value = 1     # outer.worker, outer.worker.value
    else:
        def worker(): value = 2     # outer.worker, outer.worker.value
```

Re-derived from source with the old rule:

| corpus | files with duplicate qnames | duplicate qnames | children given a non-containing parent | reference sites given a non-containing subject |
|---|---|---|---|---|
| corpus1 | 0 / 7 | 0 | 0 | 0 |
| corpus2 | 4 / 8 | 20 | 0 | 0 |
| **corpus3** | **88 / 140** | **493** (1,302 symbols) | **18** | **50** |

The 18 are what destroyed the artifacts: a forward `parent_id` violates
`symbol.parent_id REFERENCES symbol(symbol_id)` and the rollback took the
artifact row with it. **The 50 committed successfully.** corpus1 and corpus2
were correct only by luck — corpus2 has 20 duplicate names and none of them
happened to have children.

This is why your instruction not to fix it by ordering was right. Parent-first
insertion would have converted 18 loud failures into 18 silent ones and left the
50 untouched.

## 2. Identity is now source occurrence

`OccurrenceIndex` in `kgc/analysis/mapper.py` records every occurrence of a
qualified name with its byte span and id. `symbol_id` already contains the span,
so no new identifier scheme was introduced.

**Parent** — `enclosing()`: the occurrence whose span **contains** the child.
Strict, because a parent link *asserts* containment. No unique one → no parent,
plus an `UNRESOLVED_PARENT_OCCURRENCE` diagnostic.

**Reference subject** — `owner()`: the enclosing occurrence, else the sole
occurrence, else dropped with an `UNRESOLVED_REFERENCE_SUBJECT` diagnostic.

The fallback is not laxness, and finding out why it was needed is worth
recording. Requiring containment for subjects too dropped **283 real edges** on
werkzeug: a decorator's call site sits *above* its function's own byte span,
because `FunctionDef.lineno` points at `def`, not at `@`. Containment is the
**disambiguator** among same-named candidates, not the definition of belonging;
where a name is unambiguous there is nothing to choose between. The one case
refused is the one that would be a guess — several occurrences, none enclosing.

**Reference target** — a target has no source span, so it cannot be
disambiguated at all. Defined once here → that symbol. Defined several times
here, or several times in the artifact that owns it → the `reference` row
records `UNRESOLVED` with the reason, and `object_literal` keeps the name. It is
never bound to an arbitrary occurrence.

`ModuleIndex.from_store` was the last arbitrary chooser: `ids.setdefault(qname,
sid)` over a scan with **no `ORDER BY`**. A name covering several symbols now
maps to `None` (absent = not in the corpus, present-and-`None` = several
candidates), and the scan is ordered. That removed the final **2 cross-artifact
`CALLS` edges** on werkzeug that pointed at an arbitrary occurrence.

Full site-by-site audit with dispositions: `OCCURRENCE_IDENTITY_AUDIT.md`.

## 3. Insertion order, tested separately

`map_analysis` sorts its returned symbols by `(byte_start asc, byte_end desc)`.
A parent's span contains its child's, so `p0 ≤ c0` and, when `p0 == c0`,
`p1 ≥ c1` — the parent provably sorts first. This is a **persistence detail**;
`check_invariants()` enforces the identity separately by re-reading spans.

Two tests, deliberately not one:

* *identity* — two same-named parents each with a child: child A → parent A and
  child B → parent B, asserted by span containment and by the two parents being
  **distinct ids**;
* *persistence* — every `parent_id` already appears earlier in the returned
  list, and the database accepts the order with `PRAGMA foreign_keys` on
  (asserted, so the guarantee is not vacuous).

## 4. A parseable artifact now survives extraction failure

`SAVEPOINT` inside the artifact transaction (`kgc/store.py`:
`savepoint`/`release`/`rollback_to`). Graph writes are discarded; the artifact
row survives:

```
artifact metadata inserted
    SAVEPOINT extraction
        symbols / diagnostics / next-stage work item
    success -> RELEASE, commit                    complete graph + artifact OK
    failure -> ROLLBACK TO, artifact marked FAILED + parse_error + diagnostic,
               work item FAILED, commit           no partial graph, artifact kept
```

The same shape guards stage 2, which additionally purges the symbols stage 1
committed (`purge_artifact_graph`, with `PRAGMA defer_foreign_keys` because
`symbol.parent_id` is self-referential), so a `FAILED` artifact never carries
half a graph.

**Crash semantics are unchanged (§8).** `CrashPoint` is re-raised *past* the
extraction handler, so a simulated process crash still loses the whole
transaction and leaves the work item `RUNNING` for resume to requeue. A test
asserts exactly that — a crash must **not** produce a FAILED artifact — so the
new behaviour cannot quietly absorb the crash/resume guarantees. The two are
distinct exception types and a test asserts neither subclasses the other.

### A second bug, found by the new test

Writing the failure fixture immediately exposed one: `_resolve_stage` iterated
**every** enqueued file, including artifacts whose extraction had just been
rolled back. It wrote claims whose `subject_id` pointed at symbols that no
longer existed. Stage 2 now runs only where stage 1 committed a resolve work
item, and two new invariants (`claim(s) whose subject symbol does not exist`,
`whose object symbol does not exist`) make the class of error detectable rather
than silent.

## 5. Coverage postcondition (§9)

The database cannot check this — it never sees the filesystem walk. So
`ingest()` reconciles the walk against persisted artifacts and returns
`IngestReport.absent`. `kgc ingest` **exits non-zero** when it is non-empty, and
a regression test recomputes the walk independently and fails on any missing
row.

| corpus | walked analysable | OK | FAILED | SKIPPED | UNSUPPORTED | **absent** |
|---|---|---|---|---|---|---|
| corpus1 | 7 | 7 | 0 | 0 | 8 | **0** |
| corpus2 | 8 | 8 | 0 | 0 | 16 | **0** |
| **corpus3** | **140** | **140** | **0** | 0 | 85 | **0** |

Not one file is called "parsed" merely because a row exists: `OK` here means the
extract and resolve stages both committed, and `extraction_failures` is 0 on all
three corpora.

### The seven

| file | before | after | symbols |
|---|---|---|---|
| `src/werkzeug/local.py` | absent | **OK** | 166 |
| `src/werkzeug/test.py` | absent | **OK** | 193 |
| `src/werkzeug/testapp.py` | absent | **OK** | 25 |
| `src/werkzeug/debug/__init__.py` | absent | **OK** | 94 |
| `tests/test_test.py` | absent | **OK** | 236 |
| `tests/test_utils.py` | absent | **OK** | 76 |
| `tests/middleware/test_proxy_fix.py` | absent | **OK** | 9 |

corpus3: `parsed` 133 → **140**, `failed` 7 → **0**, symbols 5,303 → **6,102**,
claims 14,449 → **16,527**.

## 6. Independent verification of the repaired graph

Re-derived from persisted byte spans by `experiments/k11_integrity_audit.py`,
independently of the mapper that wrote them:

| check | corpus1 | corpus2 | corpus3 |
|---|---|---|---|
| parent links violating containment | 0 | 0 | **0** (5,962 links) |
| non-module symbols with no parent | 0 | 0 | **0** |
| claims with evidence inside the subject's span | 66 | 414 | 16,244 |
| …outside, but the subject's name is unique (decorators) | 0 | 0 | 283 |
| **…outside AND the subject's name is ambiguous** | **0** | **0** | **0** |
| non-`CONTAINS` edges to an ambiguous target | 0 | 0 | **0** |
| `check_invariants()` | `[]` | `[]` | `[]` |

`independent_claim_verification.py`, which re-implements the check against
source: definition claims **2,473** (was 2,197), sampled 400, precision
**1.000**; `EXTENDS` **163** (was 154), sampled 150, precision **1.000**;
evidence spans 400/400 byte-exact; claims without verifiable evidence 0. The
graph got 2,078 claims larger and precision did not move.

## 7. Determinism (§15)

Each corpus ingested **twice into two separate databases** from the same root
and configuration, comparing `artifact`, `symbol`, `claim`, `evidence`,
`reference` and `diagnostic` in full (ids, spans, locators, quoted text,
resolutions, reasons, messages):

| corpus | identical | differing tables |
|---|---|---|
| corpus1 | **yes** | none |
| corpus2 | **yes** | none |
| corpus3 | **yes** | none |

Duplicate-qualified-name symbols keep stable ids because `symbol_id` is
content- and span-addressed. `run_id` remains deliberately run-specific and is
not compared.

## 8. Historical evaluations: preserved, diffed, explained (§13)

K-1 results were copied to `experiments/*_K1.json` **before** anything was
re-run. Gate 2, Gate 2.25 and K-1 reports are unmodified. `*_PREFIX.json`
(Gate 2.5) is untouched.

| evaluation | differing fields |
|---|---|
| corpus2 `claim_first_results` | **0** |
| corpus2 `c2_generalization_results` | **0** — 38/38, precision 1.000 |
| `independent_verification_results` | 3, all population growth: definitions 2,197 → 2,473, `EXTENDS` 154 → 163, total claims 14,449 → 16,527. Every precision unchanged at 1.000 |
| `k1_census_results` | 5: `HAS_VALUE` 158 → **162**, diagnostics 234 → **368**, and `supported_minus_emitted` **4 → 0** |
| corpus3 `gate225_results` | see below |

The census line is the K-1 prediction closing exactly: K-1 §3 reported 162
class-body literals supported in source but only 158 claims, the gap being the
lost artifacts. Source and graph now agree at 162.

### corpus3: 2 of 132 queries changed

Every per-query row was compared field by field, not just the summary.

| id | split | class | before | after |
|---|---|---|---|---|
| `G225-0015` | CALIBRATION | exact_lookup | `ABSTAIN` (`INV-1_subject`) | **`EXPOSE`**, correct |
| `G225-0022` | TEST | exact_lookup | `ABSTAIN` (`INV-1_subject`) | **`EXPOSE`**, correct |

Both ask about symbols in `tests/test_test.py` — one of the seven. The symbols
did not exist, so `decide()` abstained with *"subject is not a compiled symbol"*
— **correct behaviour on a corpus that was missing the file**, scored as a false
abstention. No other decision moved.

| split | false support | false abstention | claim R@10 | evidence R@10 |
|---|---|---|---|---|
| CALIBRATION | 0.000 → **0.000** | 0.200 → **0.150** | 0.700 → **0.750** | 0.600 → 0.600 |
| VALIDATION | 0.000 → **0.000** | 0.048 → 0.048 | 0.857 → 0.857 | 0.762 → 0.762 |
| **TEST** | 0.000 → **0.000** | 0.143 → **0.095** | 0.810 → **0.857** | 0.524 → **0.571** |
| ALL | 0.000 → **0.000** | 0.129 → **0.097** | 0.790 → **0.823** | 0.629 → **0.645** |

Safety did not move: false support stays 0.000 in every split and every negative
class, untrusted exposure 0, ambiguity abstention 1.0, out-of-scope abstention
1.0.

### Stated plainly

**Gate 2.25, Gate 2.5 and K-1 were evaluated on an incomplete corpus3
substrate.** Seven of 140 Python files — 11.6% of its source — were absent from
the graph, and the numbers in those reports were measured on that graph. They
are not restated here and must not be read as if they were measured on the
repaired one. The direction of the error is now known: availability was
**pessimistic** (false abstention 0.129 vs 0.097 on ALL), and safety was
unaffected. The K-1 §9.1 concern — that J-1 would measure a corpus with unknown
holes — no longer applies.

## 9. Findings worth your attention

**9.1 `CALLS` from a decorator.** The 283 outside-span attributions are
decorator call sites recorded as the decorated function *calling* the decorator.
That is pre-existing modelling, not an integrity defect, and K-1.1 preserved it
rather than silently changing 283 edges. But `@app.route(...)` is a decorator
being *applied to* a function, not a call the function makes. If `CALLS` is
meant to be "this symbol's body invokes that", these 283 are mislabelled and
belong under a separate predicate. Not touched here; it is a vocabulary
question, and K-1.1 authorised neither.

**9.2 Coverage is only checkable at ingest time.** `rep.absent` is computed
where the walk and the database are both in hand. Opening an existing database
later cannot verify coverage — nothing records what the walk contained. A
`walked_files` count on `processing_run` would close it. Small, and out of scope.

**9.3 The `symbol` table permits what the mapper now forbids.** Occurrence
identity is enforced by `check_invariants()` (a full-database audit) rather than
by a trigger, because the containment predicate needs the parent row and
`json_extract` on both locators. A trigger is possible and would be stronger.
Deferred.

**9.4 §17, verified.** `STRUCTURAL_PREDICATES`, `SEMANTIC_PREDICATES` and
`STRUCTURAL_TO_SEMANTIC` in `kgc/ir.py` are **genuinely dead**: an AST-level scan
of every `.py` file finds exactly one read — `ir.py:88`, building
`STRUCTURAL_TO_SEMANTIC` from `STRUCTURAL_PREDICATES` — and nothing outside
`ir.py` reads any of them. They are **not** part of the active trust boundary,
which is `kgc.predicates.is_trusted` plus the `structural_predicate_requires_derivation`
trigger whose list is derived from `kgc.predicates.STRUCTURAL`.
`STRUCTURAL_TO_SEMANTIC` does encode ADR-0006's model-predicate rewrite — a
policy that is **written but not implemented**. Recorded as deferred technical
debt and **not deleted**, as instructed.

## 10. Still deferred (§16)

Untouched, as instructed: `ast.AnnAssign` (corpus2 still yields zero `HAS_VALUE`
claims), `what is the value of X` query routing, and cross-artifact semantic
identity. Confirmed by measurement: corpus2's `HAS_VALUE` count is still 0 and
corpus3's census is unchanged apart from the four recovered claims.

## 11. Exit conditions (§18)

| # | condition | result |
|---|---|---|
| 1 | duplicate qnames never determine parent identity by ordering | **met** — containment decides; a test asserts child A → parent A with distinct parent ids |
| 2 | parent-child occurrence mapping is source-correct | **met** — 0 violations across 5,962 links, re-derived from persisted spans |
| 3 | parseable extraction failures leave FAILED artifact records | **met** — stage 1 and stage 2 fixtures, with `parse_error` and a diagnostic |
| 4 | failed extraction leaves no partial graph | **met** — 0 symbols, 0 claims, 0 evidence for the failed artifact, both stages |
| 5 | artifact coverage for walked analysed files = 100% | **met** — absent = 0 on all three corpora; CLI exits non-zero otherwise |
| 6 | the seven corpus3 files are present | **met** — all seven `OK`, 799 symbols between them |
| 7 | all preserved invariants hold | **met** — I-1/I-2/I-3 on corpus2 and corpus3, three gold sets, `check_invariants()` empty |
| 8 | all existing tests pass | **met** — 142 (115 + 27 new) |
| 9 | historical evaluations preserved and explicitly diffed | **met** — `*_K1.json`, field-by-field, 2 changed rows explained |
| 10 | two-run determinism holds | **met** — six tables, three corpora, byte-identical |

> **PASS — K-1.1 repaired artifact integrity and occurrence identity.**

J-1 can now be run on a substrate that is known to be complete. The honest
baseline for it is still the hand-written 0.50 parse rate, not the
template-derived 0.984.

## 12. Reproducing

```bash
python3 -m unittest discover -s tests -t .              # 142 tests
python3 experiments/k11_integrity_audit.py              # coverage, identity, determinism
python3 experiments/gate225_eval.py                     # corpus3
python3 experiments/claim_first_eval.py                 # corpus2
python3 experiments/c2_generalization.py
python3 experiments/independent_claim_verification.py
python3 experiments/semantic_invariants.py
python3 experiments/k1_has_value_census.py
python3 experiments/validate_gold.py
python3 experiments/validate_gate2_gold.py
python3 experiments/validate_gate225_gold.py
```

K-1 results preserved as `experiments/*_K1.json`; Gate 2.5 results remain at
`experiments/*_PREFIX.json`.
