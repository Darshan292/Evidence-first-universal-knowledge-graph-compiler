# K-1.2 REPORT — recovery state and decorator CALLS semantics

**Date:** 2026-09-23 · **Verdict: PASS — K-1.2 repaired recovery state and decorator CALLS semantics.**

Both reported defects were real and are fixed. Two further integrity problems
surfaced while fixing them — one a latent quarantine bug that silently stopped a
database ingesting anything after three runs, one introduced by K-1.2's own
purge logic — and **both are repaired inside this gate**, each with a regression
test that fails without the fix.

170 tests pass. All three corpora: zero absent, zero failed, zero invariant
violations, two-run determinism on every table. Exactly **2 of 132 corpus3
queries changed**, in `n_hits` and `evidence_rank` only — no decision moved.

---

## 1. Reproduced first, at commit `4fe8c16`

Nothing was changed before both defects were reproduced against the K-1.1
commit in a clean worktree.

**Recovery, stage 1 and stage 2 alike:**

```
fail_extraction_at  after clean retry -> status=FAILED  claims=1
                    invariants: ['1 claim(s) belonging to a FAILED artifact (partial graph)']
fail_resolution_at  after clean retry -> status=FAILED  claims=1
                    invariants: ['1 claim(s) belonging to a FAILED artifact (partial graph)']
```

Exactly as you described: `add_artifact` is `INSERT OR IGNORE`, so a row marked
`FAILED` by an earlier run kept that status forever while a clean re-ingestion
rebuilt its graph underneath it. The invariant K-1.1 added caught the state it
created.

**Decorator CALLS**, corpus3: 8,658 `CALLS` claims, of which **283** come from
decorator expressions — matching, exactly, the 283 "outside-but-unique" subject
attributions K-1.1 §9.1 flagged without identifying.

## 2. FAILED recovers to the analyser's real status

`Store.update_artifact_status(artifact_id, parse_status, parse_error)` — the
counterpart to `mark_artifact_failed`. Called on the success path of **both**
stages, inside the same savepoint, with `an.parse_status` and `an.parse_error`
from the analyser. Nothing is forced to `OK`: whatever the backend reports is
what the row carries, and a later resolution failure can legitimately set it
back to `FAILED`.

Provenance is untouched — same `artifact_id`, same `first_seen_run`, same row,
one row. A test asserts all four.

State machine, exercised end to end:

| cycle | artifact | graph |
|---|---|---|
| fail | `FAILED`, `EXTRACTION_FAILED: …` | 0 symbols, 0 claims, 0 evidence |
| retry | `OK`, `parse_error` NULL | full graph |
| fail again | `FAILED` | purged again |
| 3 alternating cycles | correct at every step | `check_invariants() == []` throughout |

## 3. Decorator expressions are not calls the function makes

`_decorator_call_sites` collects every `ast.Call` inside any
`decorator_list` — the whole expression, so `@deco(make_key())` excludes both.
Those sites emit no `CALLS` claim and instead record
`UNSUPPORTED_DECORATOR_CALL`, naming the target and the definition.

No `DECORATES` predicate was added. The relationship is real and the evidence
was correct; only the predicate was wrong, and inventing one to hold it is a
vocabulary decision this gate did not authorise.

Verified by the fixtures you specified, plus the cases around them:

| source | result |
|---|---|
| `@app.route("/")` + `def f(): return 1` | **no** `CALLS`, one diagnostic |
| `def f(): app.route("/")` | `f CALLS app.route` |
| `@app.route("/")` + `def f(): app.route("/")` | **exactly one** `CALLS`, citing the body call; one diagnostic |
| `@deco(make_key())` | no `CALLS`, two diagnostics |
| `@property` | no claim, **no** diagnostic (never was a Call) |
| `@register()` on a class | no `CALLS` |

## 4. The CALLS semantic diff (§7)

| corpus | CALLS before | decorator CALLS removed | CALLS after | AST decorator call sites |
|---|---|---|---|---|
| corpus1 | 22 | 0 | **22** | 0 |
| corpus2 | 163 | 0 | **163** | 0 |
| **corpus3** | **8,658** | **−283** | **8,375** | **283** |

The arithmetic is checked in code against an independent AST count, not
asserted. The "before" figures were re-measured on the K-1.1 commit rather than
quoted.

**This is not a regression.** 283 claims were removed because they asserted
something false: that a function calls the decorator applied to it. The
independent identity audit confirms it from the other direction — claims whose
evidence lies outside their subject's source span fell from **283 to 0**. Every
surviving claim now cites evidence inside the symbol it is about.

## 5. Two further integrity problems, found and fixed here

### 5.1 The fourth ingestion of an unchanged corpus was silently quarantined

Not in your brief. Reproduced at `4fe8c16`:

```
run 1: parsed=3 failed=0      run 4: parsed=0 failed=3
run 2: parsed=3 failed=0      run 5: parsed=0 failed=3
run 3: parsed=3 failed=0      run 6: parsed=0 failed=3
```

`work_item.attempts` exists to quarantine an item that repeatedly kills the
process. It is keyed by content, and `finish_work` never reset it — so it
accumulated across a database's entire lifetime and every file hit
`MAX_ATTEMPTS` on the fourth run. From then on the database silently stopped
updating: `absent` stays empty (the artifacts exist from earlier runs) and
`check_invariants()` sees nothing wrong.

A completed run is evidence the item is not poisonous, so `finish_work` now
clears `attempts` on `DONE`. Genuine repeated crashes still quarantine, because
a crash never reaches `finish_work`. My K-1.1 tests never re-ingested more than
twice, which is why they missed it.

### 5.2 A failure-and-recovery cycle left 156 stale claims — introduced by K-1.2

Found by checking whether recovery restores the *original* graph rather than
merely a valid one. On corpus3, after fail-then-retry:

```
claims before 16,244   after retry 16,400   gained 156   lost 0
check_invariants(): []
```

Mechanism: purging the failed artifact removes its symbols and the edges into
it, and the corpus symbol table is rebuilt without them — so artifacts resolved
*after* the failure emit **unresolved** variants of those edges. `add_claim` is
`INSERT OR IGNORE` and `claim_id` covers the resolved object, so on the clean
retry the resolved edges came back and the unresolved variants **stayed beside
them**. 156 claims asserting `CALLS → "Client"` as unresolvable, next to the
correct resolved edge. No invariant can see it: an unresolved claim is
perfectly legal.

The root cause is that the resolve stage **appended** an artifact's claims
instead of replacing them, so the graph was a function of the database's failure
history rather than of the source. `Store.purge_artifact_claims` now drops an
artifact's own claims at the start of its resolution savepoint, before the new
ones are written. Symbols and inbound edges are untouched; they belong to a
different owner.

Verified on the full corpus:

| sequence | before | during | after | gained | lost | identical graph |
|---|---|---|---|---|---|---|
| clean / clean / clean | 16,244 | 16,244 | 16,244 | 0 | 0 | — |
| clean / stage-1 failure / clean | 16,244 | 15,707 | **16,244** | **0** | **0** | **yes** |
| clean / stage-2 failure / clean | 16,244 | 15,707 | **16,244** | **0** | **0** | **yes** |

The regression test was checked by disabling the fix: it fails with exactly the
stale `CALLS → 'target'` claim, on both stages.

### 5.3 Two smaller corrections in the same area

*Inbound edge loss is recorded.* Purging an artifact also removes claims in
other artifacts that point at its symbols — otherwise their `object_id` dangles
at a deleted row. That loss belongs to *those* artifacts, so each gets an
`INBOUND_EDGES_DROPPED` diagnostic naming the count and the reason, rather than
disappearing silently.

*The failure tally stays honest.* A stage-2 failure now moves the artifact from
`parsed` to `failed`, so `seen == parsed + failed + skipped + unsupported +
unchanged` holds. Verified on corpus3 with an injected failure: 225 = 139 + 1 +
0 + 85 + 0.

## 6. All three corpora (§6)

| | corpus1 | corpus2 | corpus3 |
|---|---|---|---|
| walked analysable / OK / FAILED / absent | 7 / 7 / 0 / **0** | 8 / 8 / 0 / **0** | 140 / 140 / 0 / **0** |
| artifacts (incl. UNSUPPORTED) | 15 | 24 | 225 |
| symbols | 39 | 172 | 6,102 |
| claims | 66 | 414 | **16,244** |
| `CALLS` | 22 | 163 | **8,375** |
| `CONTAINS` / `IMPORTS` / `EXTENDS` / `HAS_VALUE` | 32 / 11 / 1 / 0 | 164 / 76 / 11 / 0 | 5,962 / 1,582 / 163 / 162 |
| diagnostics | 21 | 104 | 5,166 |
| `check_invariants()` | `[]` | `[]` | `[]` |
| two-run determinism | **yes** | **yes** | **yes** |

corpus3 diagnostics, reconciled: `UNRESOLVED_REFERENCE` 4,460 → **4,430**
(−30: decorator sites that previously produced one), `UNSUPPORTED_DECORATOR_CALL`
0 → **283**, others unchanged. Net **+253**, which is the whole difference
between 4,913 and 5,166.

## 7. Independent verification (§8)

Re-run rather than assumed to hold.

| check | K-1.1 | K-1.2 |
|---|---|---|
| definition claims: population / sampled / precision | 2,473 / 400 / 1.000 | 2,473 / 400 / **1.000** |
| `EXTENDS`: population / sampled / precision | 163 / 150 / 1.000 | 163 / 150 / **1.000** |
| evidence spans re-read byte-for-byte | 400 / 400 | **400 / 400** |
| claims without verifiable evidence | 0 | **0** |
| corpus2 `c2_generalization` | 38/38, 1.000 | **38/38, 1.000** |
| parent links violating containment | 0 | **0** |
| non-module symbols with no parent | 0 | **0** |
| claims with evidence **outside** the subject's span | **283** | **0** |
| claims outside **and** ambiguously named | 0 | **0** |
| artifact coverage (absent) | 0 | **0** |
| invariants I-1 / I-2 / I-3 | hold | **hold** |
| three gold sets | OK | **OK** |

The last row of the identity audit is the strongest single result: after
removing decorator `CALLS`, **every** claim in the graph cites evidence inside
the symbol it is about.

## 8. Historical evaluations (§6, §13 discipline)

K-1.1 results were copied to `experiments/*_K11.json` before anything was
re-run. K-1 results remain at `*_K1.json`, Gate 2.5 at `*_PREFIX.json`. No gate
report was edited.

| evaluation | differing fields |
|---|---|
| corpus2 `claim_first_results` | **0** |
| corpus2 `c2_generalization_results` | **0** |
| `k1_census_results` | **0** — `HAS_VALUE` untouched at 162 |
| `independent_verification_results` | 1: `total_claims` 16,527 → 16,244 |
| `k11_integrity_results` | 5: claims −283, evidence −283, references −283, diagnostics +253, outside-span attributions 283 → **0** |

### corpus3: 2 of 132 rows changed, and no decision moved

| id | split | class | change |
|---|---|---|---|
| `G225-0018` | TEST | exact_lookup | `n_hits` 15 → 8, `evidence_rank` 15 → **8** |
| `G225-0020` | VALIDATION | exact_lookup | `n_hits` 3 → 2, `evidence_rank` 3 → **2** |

Both had decorator `CALLS` claims in their hit set. Removing them shortened the
list and moved the correct evidence up; `G225-0018` crossed the @10 threshold.

| split | false support | false abstention | claim R@10 | evidence R@10 | evidence MRR |
|---|---|---|---|---|---|
| CALIBRATION | 0.000 | 0.150 | 0.750 | 0.600 | unchanged |
| VALIDATION | 0.000 | 0.048 | 0.857 | 0.762 | 0.486 → **0.494** |
| **TEST** | 0.000 | 0.095 | 0.857 | 0.571 → **0.619** | 0.382 → **0.385** |
| ALL | 0.000 | 0.097 | 0.823 | 0.645 → **0.661** | 0.430 → **0.434** |

**No `decision`, `correct`, `exposed` or `failed_invariant` field changed on any
query.** Safety is untouched: false support 0.000 in every split and every
negative class, untrusted exposure 0, ambiguity abstention 1.0, out-of-scope
abstention 1.0. Removing 283 invalid claims improved evidence ranking and
changed nothing else.

## 9. Determinism (§9)

Each corpus ingested twice into separate databases, comparing `artifact`,
`symbol`, `claim`, `evidence`, `reference` and `diagnostic` in full: **identical
on all three**, no differing tables. The decorator fixture is ingested twice in
its own test and compared on symbol ids, claims, evidence and diagnostics —
including the `UNSUPPORTED_DECORATOR_CALL` messages, which carry a line number
and a resolved name and are therefore content-stable.

## 10. One limitation you should know before J-1

**The compiler is append-only across corpus revisions.** Verified:

```
ingest m.py containing old_name()   ->  m.old_name compiled
edit m.py to contain new_name()     ->  a NEW artifact version is created
re-ingest                           ->  both versions' symbols remain
decide("old_name")                  ->  EXPOSE  "compiled claim, trusted
                                                 establishment, verified evidence"
```

A deleted file keeps its artifact and its whole graph. This is pre-existing and
by design — content-addressed artifact versions, asserted by a Gate 1 test — and
entirely outside K-1.2's scope, so it was not changed. But it means a database
re-ingested after source edits will confidently answer with facts that are no
longer true, and mark them verified.

**For J-1 the mitigation is one line: ingest into a fresh database.** If
re-ingestion over a live database is ever wanted, superseding removed artifacts
is its own gate.

## 11. Exit criteria (§10)

| criterion | result |
|---|---|
| successful retry never leaves an artifact FAILED | **met** — both stages, asserted on `parse_status` and `parse_error` |
| successful retry never creates claims under a FAILED artifact | **met** — `check_invariants() == []` after every cycle |
| controlled stage-1 failure remains recoverable | **met** |
| controlled stage-2 failure remains recoverable | **met** |
| repeated failure/success cycles preserve state | **met** — 3 alternating cycles, plus the graph-identity test |
| decorator expressions never create CALLS claims | **met** — corpus3 outside-span attributions 283 → 0 |
| ordinary function-body calls still create CALLS claims | **met** — 8,375 remain, arithmetic checked against the AST |
| all invariants pass | **met** — three corpora, I-1/I-2/I-3, three gold sets |
| full test suite passes | **met** — 170 |
| all three corpora remain deterministic | **met** |

> **PASS — K-1.2 repaired recovery state and decorator CALLS semantics.**

Corrective engineering stops here. Next is J-1, with genuinely independent
queries, on a fresh database. The honest parse-rate baseline going in is still
the hand-written **0.50**, not the template-derived 0.984.

## 12. Reproducing

```bash
python3 -m unittest discover -s tests -t .              # 170 tests
python3 experiments/k12_measurements.py                 # corpora, CALLS diff, determinism
python3 experiments/k11_integrity_audit.py              # coverage, identity, determinism
python3 experiments/independent_claim_verification.py
python3 experiments/c2_generalization.py
python3 experiments/gate225_eval.py
python3 experiments/claim_first_eval.py
python3 experiments/semantic_invariants.py
python3 experiments/k1_has_value_census.py
python3 experiments/validate_gold.py
python3 experiments/validate_gate2_gold.py
python3 experiments/validate_gate225_gold.py
```

Preserved: `experiments/*_K11.json` (K-1.1), `*_K1.json` (K-1),
`*_PREFIX.json` (Gate 2.5).
