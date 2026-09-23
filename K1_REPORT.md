# K-1 REPORT — HAS_VALUE for direct class literals

**Date:** 2026-09-23 · **Verdict: FAIL — K-1 exposed a deeper architectural problem.**

The implementation is finished and stable: 115 tests pass, functional conflict
now runs end to end on compiler output, determinism holds on 225 artifacts, and
**no historical metric moved on any corpus**. If the verdict were only about the
code it asked for, this would be a PASS.

It is not, because measuring HAS_VALUE coverage — which §7 required — surfaced
two problems that change what should happen next, one of them a silent data
loss that **invalidates the substrate J-1 would measure on**. §9 and §10 state
them. Everything before that is the work as specified.

---

## 1. Exact syntax supported

Emitted only when every condition holds. `kgc/analysis/python_backend.py`,
`_class_literal` plus the `class_body` walk flag.

| # | Condition |
|---|---|
| 1 | the statement is a **direct child of a `ClassDef` body** |
| 2 | it is an `ast.Assign` with **exactly one** target |
| 3 | that target is an `ast.Name` |
| 4 | the right-hand side is a bare `ast.Constant` |
| 5 | the constant's type is `int`, `float`, `bool`, `str` or `NoneType` |

```python
class Config:
    TIMEOUT = 30            # HAS_VALUE  "30"
    NAME = "production"     # HAS_VALUE  '"production"'
    ENABLED = True          # HAS_VALUE  "True"
    RATIO = 0.5             # HAS_VALUE  "0.5"
    MISSING = None          # HAS_VALUE  "None"
```

`bytes` is excluded deliberately, and this is not tidiness: `kgc/claim_value.py`
strips the `b` prefix, so `b"30"` and `"30"` would compare **SAME** when they are
different values. Admitting bytes would manufacture a false agreement.

## 2. Exact syntax rejected

Every one records a diagnostic naming the reason. Nothing is evaluated: no
arithmetic, no constant folding, no symbolic evaluation, no data flow.

| Source | Recorded reason |
|---|---|
| `TIMEOUT = 10 + 20` | `value is a BinOp, not a bare literal` |
| `TIMEOUT = get_timeout()` | `value is a Call, not a bare literal` |
| `TIMEOUT = SOME_OTHER_VALUE` | `value is a Name, not a bare literal` |
| `NEGATED = -1` | `value is a UnaryOp, not a bare literal` |
| `A = B = 10` | `chained assignment to several targets` |
| `A, B = (1, 2)` | `Tuple target is not a plain name` |
| `obj.attr = 5` | `Attribute target is not a plain name` |
| `ANNOTATED: int = 7` | `annotated assignment` |
| `RAW = b"30"` | `literal type 'bytes' is not safely comparable` |

Three contexts are outside the declared grammar and produce neither a claim nor
a diagnostic, because they are not class-body assignments at all: module-level
constants, assignments nested in an `if`/`try` inside a class body, and anything
in a function or method body. A test asserts all three stay empty.

`-1` being refused is worth stating plainly: Python parses it as `UnaryOp(USub,
Constant(1))`, not as a negative constant. Admitting it means evaluating an
expression, which rule 4 forbids, so **no negative number is extracted today**.

## 3. HAS_VALUE claim count, measured

`experiments/k1_has_value_census.py`, counted from the same AST the backend
walks — not estimated.

| | corpus2 (itsdangerous, 24) | corpus3 (werkzeug, 225) |
|---|---|---|
| direct class-body assignments | 8 | 530 |
| **supported → HAS_VALUE** | **0** | **162** |
| refused, with a diagnostic | 8 | 368 |
| claims actually in the graph | 0 | **158** |
| diagnostics recorded | 8 | 234 |
| not reached: module level / nested | 6 | 226 |
| not reached: function bodies | 96 | 3,838 |

Refusals on corpus3: `Call` 184, annotated 141, `Name` 18, `Tuple` 11,
`Attribute` 9, `Dict` 2, `Subscript` target 2, chained 1.

**corpus2 produces zero HAS_VALUE claims.** All eight of its class-body
assignments are annotated (`x: int = 3`), which rule 2's "exactly one `ast.Name`
target" reading excludes as `ast.AnnAssign`. That is 100% of one independent
corpus and 27% of the other. I did not extend the grammar, because §1 said not
to broaden it during this step — but the honest summary is that on a
modern type-annotated codebase this predicate currently extracts **nothing**,
and admitting `ast.AnnAssign` with a `Name` target and a `Constant` value is a
four-line change that needs no evaluation and no new machinery.

### What this capability is

Deterministic extraction of simple direct class-body literals. Nothing broader.
It does not understand Python values: it does not evaluate, fold, resolve names,
follow assignments, read module constants, handle negative numbers, or touch
annotated assignments.

## 4. Evidence verification

| | result |
|---|---|
| HAS_VALUE claims with EXACT or REPRODUCIBLE evidence | **158 / 158 = 1.000** |
| establishment | `DERIVED` only |
| evidence span | the **assignment statement**, byte-exact, re-parses as `ast.Assign` |
| `object_id` | always `NULL` — a literal is not a symbol reference |
| claim id / evidence id | recomputed from `kgc/ids.py` and compared, all equal |
| `check_invariants()` on both corpora | `[]` |

Evidence is the whole assignment, not just the right-hand side: `TIMEOUT = 30`
is what a reader must see to check the claim; `30` alone proves nothing.

Three boundary problems the predicate hit, and how each was resolved:

**The resolver mangled it.** `resolve()` routed every non-`CALLS` reference
through `_resolve_import`, which asked whether `30` was a module in the corpus
and demoted a fully determined fact to `UNRESOLVED` with a nonsense reason.
Literal-object predicates now bypass reference resolution; the set is derived
from the spec (`LITERAL_OBJECT`), not retyped.

**The `reference` table would have broken an existing invariant.** A row saying
`DETERMINISTIC` with a null `object_id` is exactly what
`check_invariants` reports as an unsupported assertion. A literal has no target
symbol to resolve, so it gets no resolution-detail row.

**A new invariant, enforced in the database.** `literal_claim_carries_its_value`
aborts any literal-valued claim written without `object_literal`, or carrying an
`object_id`. Its predicate list is derived from the spec.

## 5. Conflict tests are now end to end

All three run source → ingest → `decide()`. Nothing is inserted.

| Fixture | Query | Outcome |
|---|---|---|
| `class Server: PORT = 30; PORT = 60` | `Server's PORT` | **EXPOSE_CONFLICTED**, `CONTRADICTS`, `HAS_VALUE`, values `['30','60']` |
| `class Cache: TTL = 30; TTL = "30 seconds"` | `Cache's TTL` | **EXPOSE**, `CONSISTENT` |
| `class Solo: LIMIT = 5` | `Solo's LIMIT` | **EXPOSE**, one value |
| two `CALLS`, two `EXTENDS` beside a live `HAS_VALUE` | `what does Service.run call` | **EXPOSE**, not a dispute |

A test asserts those conflicting claims carry `extractor_id = python_ast` and
`establishment = DERIVED`, so the demonstration cannot quietly revert to
injected rows. The multi-valued regression asserts a `HAS_VALUE` claim exists in
the same corpus, so it cannot pass because nothing was compiled.

`tests/test_predicate_scope_semantics.py` lost its direct-insertion conflict
demonstration and its `if abstained: fall back to calling _conflict directly`
branch. `HAS_DEFAULT` injection survives in one test, relabelled, because that
predicate is still vocabulary-only.

### One deviation from the §6 fixture, stated

§6 asks for `TIMEOUT = 30` and **another source** asserting `30 seconds`. That
cannot be built. See §9.2 — both assertions must live in one class body, because
a functional conflict between two artifacts is unreachable today.

## 6. Reproducibility

`eval/corpus3`, 225 artifacts, ingested twice into two separate databases from
the same root and configuration:

| table | result |
|---|---|
| artifact, symbol, claim, evidence, reference, diagnostic | **byte-identical** |
| HAS_VALUE claims | 158 in both runs |
| `check_invariants()` | `[]` in both runs |

No random identifiers. `run_id` remains deliberately non-deterministic and is
not part of any compared row.

## 7. Historical regression: nothing moved

| Evaluation | Result |
|---|---|
| corpus3 `gate225_eval` — every headline metric | **identical** |
| corpus3 — **all 132 per-query rows, all 14 fields** including `n_hits` | **0 changed** |
| corpus2 `claim_first_eval` | **0 differing fields** |
| corpus2 `c2_generalization` | **0 differing fields**, 38/38, precision 1.000 |
| `independent_claim_verification` | unchanged except `total_claims` |
| invariants I-1 / I-2 / I-3 on corpus2 and corpus3 | hold |
| all three gold sets revalidated | OK |

Only three numbers in the whole repository changed, and all three are additions:

| | before | after | why |
|---|---|---|---|
| corpus3 claims | 14,291 | 14,449 | **+158**, exactly the HAS_VALUE count |
| corpus3 diagnostics | 3,982 | 4,216 | **+234**, exactly the refusals recorded |
| corpus3 evidence | 14,275 | **14,275** | a HAS_VALUE claim shares the `CONTAINS` claim's span, so its evidence row already existed |

No gold, burned TEST split, historical result file or benchmark conclusion was
modified. Baselines were captured before the change and diffed field by field —
the Gate 2.5 lesson was that a metric which accepts a set of outcomes cannot see
a decision change, so the per-query rows were compared directly, not the summary.

## 8. The two cleanups

**§8 — establishment.** `TRUSTED_ESTABLISHMENT` is gone from `claimfirst.py`,
which now holds no establishment policy at all; a test greps the file and fails
if any of the four levels is typed there again. `is_trusted(predicate,
establishment)` in `kgc/predicates.py` is the single authority, and a test edits
`CANONICAL["HAS_VALUE"]` — nothing else — and watches the decision change.

**Your premise was half wrong, and acting on it literally would have weakened
the boundary.** `allowed_establishment` and trust-for-answering are not the same
question:

* `allowed_establishment` — what may be **stored**. `HAS_PURPOSE` permits
  `PROPOSED`, so a model may record one.
* `TRUSTED_ESTABLISHMENT` — what may be **answered with**. A model proposal is
  storable and queryable but never a trusted answer.

They coincide for structural predicates and diverge for semantic ones. Deferring
entirely to `allowed_establishment` would have made a `PROPOSED HAS_PURPOSE`
claim exposable — dismantling the boundary Gate 2.25 built, while looking like a
simplification. Both rules now live in `kgc/predicates.py` and `is_trusted`
applies both; a test named
`test_storable_is_not_the_same_question_as_answerable` exists so the next reader
cannot collapse them by accident. Behaviour is unchanged on every corpus.

**§9 — dead code.** `is_canonical()` has no caller anywhere: no import, no
dynamic access, no `getattr`, no string reference. Removed.

## 9. What K-1 exposed

### 9.1 Seven corpus3 artifacts — 11.6% of its Python source — silently vanish

Not caused by K-1. Reproduced identically at commit `9348473` with identical
symbol counts. Found only because §7 forced me to reconcile 162 supported
literals in source against 158 claims in the graph.

```
extract  src/werkzeug/local.py              FOREIGN KEY constraint failed
extract  src/werkzeug/test.py               FOREIGN KEY constraint failed
extract  src/werkzeug/testapp.py            FOREIGN KEY constraint failed
extract  src/werkzeug/debug/__init__.py     FOREIGN KEY constraint failed
extract  tests/test_test.py                 FOREIGN KEY constraint failed
extract  tests/test_utils.py                FOREIGN KEY constraint failed
extract  tests/middleware/test_proxy_fix.py FOREIGN KEY constraint failed
```

**Root cause**, confirmed rather than guessed: `map_analysis` keeps one id per
qualified name (`by_qname[qn] = sid`), so when a name is defined twice — a
nested `def` under two conditional branches, or a local shadowing one — the
*last* id wins. A child of the *first* occurrence is then given a `parent_id`
whose row is inserted later, violating `symbol.parent_id REFERENCES symbol`.
The exception rolls the artifact's whole transaction back.

**Why this is architectural, not cosmetic.** The rollback removes the `artifact`
row too. So a file that parses perfectly leaves *no* artifact, *no* diagnostic
and *no* trace except an `error` string on a `work_item`. `check_invariants()`
reports nothing. The ingest report calls it `failed: 7` next to `parsed: 133`
and every downstream number is quoted as "225 artifacts".

That directly contradicts the rule this project has enforced since Gate 1: **an
unparseable file is a recorded fact, never an absence.** Here a *parseable* file
is an absence.

**Why it blocks J-1.** 4,326 of corpus3's 37,273 Python lines — 11.6% — are not
in the graph, including `local.py` and `test.py`, two of werkzeug's most
imported modules. An independent query author writing J-1 questions will read
the repository, not the database. Questions about those files will abstain, and
the abstention will be scored as a false abstention caused by the claim model
when its real cause is that the file was never compiled. **J-1's central number
would be measuring a corpus with holes in it, and we would not know which
holes.** corpus1 and corpus2 are unaffected, which is exactly why 84 tests and
two gates never caught it.

### 9.2 A functional conflict between two artifacts is unreachable

Subject identity is per-artifact: `symbol_id` includes the artifact id and byte
offsets. Two files each defining `class Config: TIMEOUT = ...` produce two
different qualified names, so `decide()` correctly returns `ABSTAIN_AMBIGUOUS`
before any conflict check runs. The only way two `HAS_VALUE` claims can share a
subject is a name assigned twice **inside one class body**.

So the scenario the conflict model exists for — *two sources assert incompatible
values about the same thing* — still cannot be produced, and your §6 fixture
cannot be written as specified. What K-1 delivers is real and it exercises the
FUNCTIONAL branch through the compiler, but it exercises *redefinition within a
file*, not *disagreement between sources*.

This is not fixable by emitting more predicates. It needs cross-artifact entity
resolution — deciding that `a/config.py:Config` and `b/config.py:Config` are or
are not the same entity — which the architecture deliberately does not do, and
which is a much larger question than J-3.

### 9.3 The query form that names HAS_VALUE cannot reach it

Measured on a corpus containing exactly one `HAS_VALUE` claim:

| query | reaches it |
|---|---|
| `Config.TIMEOUT` | yes |
| `Config's TIMEOUT` | yes |
| `what is the TIMEOUT of Config` | yes |
| `Config.TIMEOUT in app.py` | yes |
| **`what is the value of Config.TIMEOUT`** | **no — ABSTAIN** |
| **`what is the value of Config`** | **no — ABSTAIN** |

The IR parses these correctly and sets `predicate=HAS_VALUE`. `decide()` then
ignores `c.predicate` on the property path and looks for a *child symbol named
"value"*, finds none, and abstains with `no compiled claim provides predicate
HAS_VALUE`. The same holds for `what is the default of Service` and
`HAS_DEFAULT`. Not fixed here: §14 forbids modifying retrieval, and this belongs
with J-2.

### 9.4 Two Gate 2.5 report claims were false

Starting work, `kgc/predicates.py` at commit `9348473` contained **two identical
copies of the entire `CANONICAL` table** and a stray, broken module-level
`may_contradict(self)` — a botched edit that tests could not see, because the
later definitions shadowed the earlier ones. `is_canonical` was still present.

GATE_2_5_REPORT.md §8 says *"Removed: the superseded predicate table, the dead
`is_canonical`…"* and *"Verified: no duplicate predicate metadata outside the
spec"*. The first is false; the second is true only because of the words
"outside the spec" — the duplication was inside it. My own simplification check
was scoped so it could not see the file it was checking. Both are fixed here.

### 9.5 The structural trigger had drifted from the vocabulary — fixed

`STRUCTURAL_SQL_LIST` in `store.py` was hand-typed and omitted `HAS_VALUE`,
`HAS_DEFAULT` and `HAS_TYPE`, so *"a model may never establish a parser-level
structural fact"* stopped covering them the moment the compiler emitted one. The
list is now derived from `kgc.predicates.STRUCTURAL` (plus `REFERENCES` and
`IMPLEMENTS`, which are not in the vocabulary but have always been guarded), and
a test proves a `PROPOSED HAS_VALUE` insert is rejected by the database. I
treated this as in scope because K-1 is what turned the gap from theoretical
into real.

## 10. Simplification pass (§12)

| Check | Result |
|---|---|
| duplicate establishment rules | **removed** — one authority, `kgc/predicates.py`, grep-asserted |
| duplicate value parsing | none — the backend identifies literals, `claim_value.py` compares them; no `ast.literal_eval` anywhere |
| duplicate literal handling | none — one classifier, `_class_literal` |
| query-specific exceptions | none added |
| new wrapper layers / interfaces | none — no `ValueExtractor`, no class, no registry; two functions on the existing AST path |
| duplicate predicate metadata | **removed** (§9.4) and **de-duplicated** (§9.5) |

One duplication found and deliberately **not** removed: `kgc/ir.py` defines
`STRUCTURAL_PREDICATES`, `SEMANTIC_PREDICATES` and `STRUCTURAL_TO_SEMANTIC`,
all three with **zero callers** anywhere in the repository, duplicating
`kgc.predicates.STRUCTURAL` / `SEMANTIC` and disagreeing with them.
§9 authorised removing `is_canonical`, not a general purge, and
`STRUCTURAL_TO_SEMANTIC` encodes ADR-0006's model-rewrite policy — deleting
documented policy is your call, not mine. Recommended for the next step.

## 11. Remaining limitations

1. **corpus2 extracts nothing** — annotated assignments are excluded (§3).
2. **No negative numbers**, because `-1` is an expression (§2).
3. **Module-level constants are out of scope** — 226 in corpus3, and the most
   common shape a configuration constant actually takes.
4. **Cross-artifact functional conflict is unreachable** (§9.2).
5. **`what is the value of X` abstains** (§9.3).
6. `HAS_DEFAULT`, `HAS_TYPE`, `HAS_PURPOSE`, `DEFINES`, `READS`, `WRITES` are
   still vocabulary-only; six of eleven predicates have no producer.
7. Everything deferred to J-1 … J-5, and the boundary is still exercised on one
   language.

## 12. Verdict

> **FAIL — K-1 exposed a deeper architectural problem.**

To be precise about what failed, because the binary hides it: the implementation
is complete, tested and stable. §1–§8 and §10–§12 of your instruction are done,
115 tests pass, determinism holds at 225 artifacts, and not one historical
number moved.

The verdict is FAIL because §9.1 is decision-relevant and cannot wait: **a
parseable file can disappear from the graph leaving no artifact, no diagnostic
and no invariant violation — 11.6% of corpus3's Python source today.** A system
whose thesis is that absence must always be recorded currently cannot detect its
own. Every corpus3 number quoted in three gate reports was computed on a graph
with unknown holes, and J-1 — the reality check the whole roadmap now hangs
on — would be run on that same graph.

§9.2 is the second reason, and it is about K-1's own objective: the functional
branch is now exercised by compiler output, but only for redefinition inside one
file. *Two sources disagreeing* — the case the conflict model exists for —
remains unreachable and unproven.

### Recommended before J-1

1. Make artifact loss impossible to miss: insert symbols parent-before-child (or
   defer the FK), and add an invariant that every walked, parseable, non-rejected
   file has an `artifact` row — a failed extract must leave a `FAILED` artifact
   with a reason, exactly as a syntax error does.
2. Re-run all three corpora and republish the counts. If corpus3 metrics move,
   the earlier gate numbers were measured on an incomplete graph and should be
   restated rather than quietly superseded.
3. Then J-1, with an independent query author, on a corpus known to be whole.

Admitting `ast.AnnAssign` (§3) and deleting the dead `kgc/ir.py` sets (§10) are
one-line-scale follow-ups that need your ruling, not a gate.

## 13. Reproducing

```bash
python3 -m unittest discover -s tests -t .          # 115 tests
python3 experiments/k1_has_value_census.py          # §3 coverage, both corpora
python3 experiments/semantic_invariants.py          # I-1, I-2, I-3
python3 experiments/gate225_eval.py                 # corpus3
python3 experiments/claim_first_eval.py             # corpus2
python3 experiments/c2_generalization.py
python3 experiments/independent_claim_verification.py
python3 experiments/validate_gold.py
python3 experiments/validate_gate2_gold.py
python3 experiments/validate_gate225_gold.py
```
