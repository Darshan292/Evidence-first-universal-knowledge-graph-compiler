# J-1 REPORT — Independent Query Reality Test

**Date:** 2026-09-23 · Protocol pre-registered in `J1_PROTOCOL.md` before any query was seen.

## Executive result

```
queries                = 54   (42 answerable, 6 unsupported, 4 ambiguous, 2 out of scope)
full parse rate        = 0.167   (0.190 on answerable questions)
false support          = 0.000   (0 of 12 must-not-answer questions)
answerable exposure    = 0.000   (0 of 42)
answer correctness     = not measurable — nothing was exposed
evidence sufficiency   = not measurable — nothing was exposed
```

**The system answered none of the 54 independent questions.** It also produced
no wrong answer. Both runs, from two independently built fresh databases, were
byte-identical.

| | |
|---|---|
| query set | `eval/J1_INDEPENDENT_QUERIES.json`, sha256 `fac1e7ec10f34498b75d6563be3cd827f7f208f7db4abd5fef1375014a1e2083` |
| gold | `eval/J1_GOLD.json`, sha256 `883f4bb535e405b7bb4651c9d4eb80b3bb22360e586f3a255d63300b949f6360` |
| corpus | Werkzeug `6048fa48`, content hash `9a7ca7ce517f…`, 225 artifacts, 6,102 symbols, 16,244 claims |
| compiler | `ed2fdfe`, schema 1, `python_ast/1.0.0` |
| runs | 2 from fresh databases, **identical in every row and every count** |

Decisions: `ABSTAIN` 51, `ABSTAIN_AMBIGUOUS` 3, `EXPOSE` **0**.
Parse: `PARSED` 9, `PARTIAL` 44, `AMBIGUOUS` 1.

## Comparison to the two prior numbers

| measurement | figure | what it measures |
|---|---|---|
| **J-1, independent author** | **0.190** | full parse rate on answerable questions nobody wrote for this parser |
| Gate 2, hand-written | 0.50 | queries I wrote by hand for corpus2 |
| Gate 2.25, template-derived | 0.984 | **not a baseline** |

The 0.984 is not an independent measurement and was never treated as one here.
Those queries were built from the same shapes the IR recognises, so the figure
reports how consistently the parser matches its own templates. Feeding a parser
its own grammar and reporting the hit rate measures self-consistency, not
availability.

The 0.50 is the honest prior, and it was optimistic by a factor of 2.6: I wrote
those queries, and even trying to write them naturally I wrote toward what I
knew the parser could take. **0.190 is the first number here produced by a query
set that owes nothing to the system.**

## Failure breakdown

Every failure is classified twice, because one number alone misleads
(`J1_ERROR_ANALYSIS.md` §1).

| cause | first blocker | **binding constraint** |
|---|---|---|
| parser | 40 | 9 |
| claim vocabulary | 1 | **33** |
| ambiguity handling | 1 | 0 |
| retrieval | 0 | 0 |
| evidence | 0 | 0 |
| identity | 0 | 0 |
| true corpus limitation | 0 | 0 |

"First blocker" is what stopped the query first. "Binding constraint" is what
would still stop it if everything earlier were fixed. **A perfect parser would
answer 9 of 42.** For the other 33 the fact is not in the graph in any form.

No failure was caused by retrieval, evidence, or identity — the three things six
gates of corrective work were spent on.

## Claim coverage (§7)

For the 42 answerable questions:

| | fully | partially | not at all |
|---|---|---|---|
| an emitted predicate suffices | **1** | 8 | **33** |
| the claim is actually present | 4 | 6 | **32** |
| the parser represents the question | 1 | 1 | **40** |
| the decision succeeds | **0** | — | 42 |

By the kind of fact the question needs:

| needs | n | predicate suffices |
|---|---|---|
| **behavioural — what the code does, or when** | **17** | 0 |
| parameter default | 6 | 0 |
| docstring prose | 4 | 0 |
| non-Python artifact (`pyproject.toml`, `CHANGES.rst`) | 4 | 0 |
| literal in a function body / module constant | 2 | 0 |
| class-body literal | 4 | 1 full, 3 partial |
| structural relationship | 5 | 5 partial |

## The two things worth knowing beyond the numbers

**One question had its fact compiled and still failed.** `J1-006` asks
`Response`'s default status. `werkzeug.sansio.response.Response.default_status =
200` is in the graph, `DERIVED`, byte-exact. The system abstained because the
parser extracted the subject as the word **"What"**. The same question also asks
for `default_mimetype`, which is an `ast.AnnAssign` — excluded in K-1 and
deferred — so even with a perfect parser the answer would have been half.

**`PARSED` over-reports, and safety held by luck.** Six of the nine `PARSED`
queries had bound the subject to the literal word "What", because the possessive
pattern matches *"What's the …"*. Both fields the shape requires were present, so
the IR reported a full parse of a sentence it had entirely misread. **No wrong
answer resulted** — in all six the mis-parsed subject was not a compiled symbol,
so it abstained, for the wrong reason. The guard that was supposed to catch this
is `ParseStatus`, and it reported success. This is the most dangerous thing J-1
found, and it is a latent hazard rather than a realised failure.

## The five questions

### 1. Are independent users able to express useful questions?

**Yes, and this is not the problem.** The author produced 54 questions that read
like real developer traffic — defaults, limits, security behaviour, migration
and deprecation, how parts fit together — and 42 were genuinely answerable from
the source, which I verified line by line. Some are sharp enough to catch a real
inconsistency in Werkzeug itself: `J1-004` noticed that the
`generate_password_hash` docstring says 600,000 pbkdf2 iterations while
`DEFAULT_PBKDF2_ITERATIONS` is 1,000,000.

The users are fine. **6 of 54 questions could be phrased in a way this system's
query IR can represent.**

### 2. Does the current deterministic graph contain the facts required?

**No.** Fully for 1 of 42, partially for 8 more, not at all for 33. The graph
holds five predicates — containment, calls, imports, inheritance, and class-body
literals — and independent developers asked about behaviour, parameter defaults,
docstring semantics and project configuration.

### 3. Is safety preserved on unsupported questions?

**Yes on the measurement, and the measurement is nearly vacuous.** 0 of 12 false
support, 0 unsafe exposure, 0 incorrect resolution on ambiguous questions.

But the system abstained on **100% of everything**, answerable included. A system
that always says "I cannot establish that" scores perfect safety on every safety
metric ever devised. What J-1 shows is that the abstention machinery does not
crash and does not leak; it does **not** show that it discriminates, because it
was never asked to choose. The safety claim from Gates 2 through 2.25 —
0.000 false support — remains untested in the only situation that matters,
which is a system that answers things.

### 4. Is the dominant limitation parser coverage, claim coverage, or retrieval?

**Claim coverage, with parser coverage in front of it. Retrieval is not
implicated at all.**

33 of 42 are blocked by the claim vocabulary even with a perfect parser. 40 of 42
are blocked by the parser first. Both must be fixed for any question to work, and
fixing either alone changes nothing: the parser alone unlocks 9, the claims alone
unlock 0 (nothing reaches them).

Of the 9 that a perfect parser would unlock, 1 is fully covered and 8 partially.

### 5. Is the project worth proceeding to J-3?

This is your decision. The evidence, stated without softening:

**J-3 as scoped addresses at most 9 of 42 questions.** Emitting `HAS_DEFAULT`,
module-level `HAS_VALUE` and `ast.AnnAssign` would cover the 6 parameter-default
questions and complete 3 class-body ones. That is real, it is cheap, and it is
about a fifth of what was asked.

**The largest group is unreachable by this architecture.** 17 of 42 questions —
40% — ask what the code does and when. "How does `safe_join` stop traversal",
"at what point does an upload spill to disk", "how does the test client carry
cookies between requests". No deterministic structural predicate expresses
behaviour. Not `HAS_DEFAULT`, not `HAS_PURPOSE`, not any predicate of that
family. Answering them needs either prose extraction from docstrings with an
evidence model that tolerates prose, or the thing this project has ruled out
since Phase 0.

**The parser is the binding constraint on everything, including the parts that
do work.** A rule-based IR with nine relation patterns and three property
patterns met 54 natural sentences and fully understood 9, six of those wrongly.
Widening it by hand is a treadmill; the architecture's own rule — no LLM in any
role — is what forbids the obvious alternative.

My reading, which is a recommendation and not a finding: **the honest next step
is not J-3.** J-3 makes a system that answers a fifth of the questions instead
of none, and it does not touch the 40% that asks about behaviour or the parser
that blocks everything. Before spending that, the product thesis deserves the
question J-1 actually put to it: *is a deterministic, evidence-first structural
graph the right shape for what developers ask?* On this evidence it answers a
minority of it, and the minority it answers is the part a competent `grep` also
answers.

What J-1 does **not** say: that the engineering is bad. The substrate is
complete and verified, precision is 1.000 where it is measurable, determinism
holds, and safety has never once leaked. That is a solid foundation. The question
is whether it is a foundation for the thing that was intended.

## Reproducing

```bash
python3 experiments/j1_eval.py --run 1        # fresh database, 54 queries
python3 experiments/j1_eval.py --run 2        # second fresh database
python3 experiments/j1_score.py               # writes J1_RESULTS.json
```

Artefacts: `J1_PROTOCOL.md` (pre-registered), `eval/J1_INDEPENDENT_QUERIES.json`
(frozen), `eval/J1_GOLD.json` (frozen), `eval/J1_JUDGEMENTS.json`,
`J1_RESULTS.json`, `J1_ERROR_ANALYSIS.md`, `eval/j1/J1_SUBSTRATE.json`,
`experiments/j1_raw_run{1,2}.json`.

## Two contamination risks, recorded rather than hidden

1. The independent author is a language-model instance, not a human developer.
   It was isolated from the system under test — no access to `kgc/`, the query
   grammar, `decide()`, retrieval, any gold file or any previous result — which
   is the contamination that matters most. It still shares a training
   distribution with me, so its phrasing may correlate with mine in ways a real
   user's would not. A human-authored set remains the stronger experiment.
2. On finishing, the author volunteered a summary of which of its questions it
   believed answerable or ambiguous. I read that before labelling gold. I did not
   use it, and it was wrong in at least one place (`Database` occurs once, not
   several times), which is why every gold label carries file, line and verbatim
   source text and can be checked against the corpus by anyone.

## Stop

J-1 stops here, per §11. The largest failure bucket is not being implemented.
No compiler-correction gate is opened: nothing J-1 found is an integrity failure.
The `PARSED` over-reporting (§ above), the unresolved `EXTENDS`/`IMPORTS` edges
and the `ast.AnnAssign` exclusion are recorded for the review, not acted on.
