# J-1 ERROR ANALYSIS

Every failure classified after the evaluation ran, per `J1_PROTOCOL.md` §6. No
engineering fix was designed during classification, and nothing in the system
was changed.

---

## 1. Two attributions, and they disagree

Each failure is classified twice, because one number alone would mislead.

**First blocker** — the first stage of the pipeline that stopped the query.
**Binding constraint** — what would *still* stop it if everything earlier were
fixed. This is the `root_cause` field, and it is the one that answers the gate's
question.

| cause | first blocker | binding constraint |
|---|---|---|
| `PARSER_GAP` | **40** | 9 |
| `CLAIM_VOCABULARY_GAP` | 1 | **33** |
| `AMBIGUITY_HANDLING` | 1 | 0 |
| others | 0 | 0 |

Read only the first column and the answer is "fix the parser". Read the second
and the answer is: **fixing the parser perfectly would answer 9 of 42 questions.
For the other 33 the graph does not contain the fact in any form.**

The disagreement is the result. Both stages block; the deeper one is the claim
vocabulary.

## 2. What kind of fact each answerable question actually needs

Classified by the nature of the fact, not by the phrasing.

| the question needs | n | an emitted predicate suffices |
|---|---|---|
| **behavioural** — what the code does, or when | **17** | none (0/17) |
| parameter default (`def f(x=10)`) | 6 | none (0/6) |
| prose in a docstring or versionchanged note | 4 | none (0/4) |
| a non-Python artifact (`pyproject.toml`, `CHANGES.rst`) | 4 | none (0/4) |
| a literal inside a function body, or a module-level constant | 2 | none (0/2) |
| a class-body literal | 4 | 1 fully, 3 partially |
| a structural relationship (contains / calls / extends) | 5 | 5 partially |
| **total** | **42** | **1 fully, 8 partially, 33 not at all** |

The largest single group — **17 of 42, 40%** — asks what the code *does*:
"how does safe_join stop traversal", "at what point does an upload go to disk",
"how does the test client carry cookies between requests". A deterministic
structural extractor has no predicate for behaviour, and none of the four
declared-but-unemitted predicates (`HAS_DEFAULT`, `HAS_TYPE`, `HAS_PURPOSE`,
`DEFINES`) would express it either.

The second group is the one J-3 was scoped around: **6 parameter defaults**,
which `HAS_DEFAULT` is exactly designed for and which the compiler has never
emitted.

## 3. The parser failures, in detail

### 3.1 Natural questions do not parse (33 of 42 answerable)

`PARTIAL` on 33, `AMBIGUOUS` on 1. The IR recognises a named entity but no
relation or property pattern matches the sentence, so it refuses rather than
answering a different question. That refusal is the design working: Gate 1.75
introduced `ParseStatus` precisely so that a half-understood query never
silently becomes another query.

It is also why the availability number is what it is.

### 3.2 `PARSED` over-reports — a latent hazard, not a realised failure

Nine queries reported `PARSED`. **Six of them had extracted the subject as the
literal word "What".**

```
J1-006  "What is the default status code and mimetype for a Response object?"
        parse_status = PARSED   subject = "What"   → ABSTAIN "subject 'What' is
        not a compiled symbol"
```

The possessive pattern `(?P<ent>[\w.]+)'s\s+(?P<prop>[\w_]+)` matches
*"What's the …"*, binding `ent="What"` and `prop="the"`. Both fields the shape
needs are present, so the IR reports a full parse of a sentence it has entirely
misread.

**State this precisely: no wrong answer was produced.** In all six cases the
mis-parsed subject was not a compiled symbol, so the system abstained — for the
wrong reason, but it abstained. Safety held by luck of the vocabulary, not by
the guard that was supposed to hold it. `PARSED` currently means "the fields are
filled", not "the question was understood", and the documented contract in
`constraints.py` claims the latter.

Classified as `PARSER_GAP`, not `IMPLEMENTATION_DEFECT`: the pattern does what
it was written to do, and no invariant is violated. It is recorded here as the
single most dangerous thing J-1 found, because the next query set might contain
a sentence beginning with a word that *is* a compiled symbol.

### 3.3 The one case where the fact was present and the parser still lost it

`J1-006` asks for `Response`'s default status. The graph contains it:

```
HAS_VALUE  werkzeug.sansio.response.Response.default_status  →  200
```

compiled, `DERIVED`, with byte-exact evidence. The system abstained because the
subject parsed as "What". This is the clearest single demonstration that the two
gaps compound: the claim layer and the query layer each have to work, and here
the claim layer did.

The same question also asks for `default_mimetype`, which is
`default_mimetype: str | None = "text/plain"` — an `ast.AnnAssign`, deliberately
excluded in K-1 §3 and deferred. So even with a perfect parser this question
would have been answered **half**. K-1's deferred decision has a measured cost
here, in the one question that came closest to working.

## 4. Ambiguity at 140 files

Three of the four `ambiguous` questions were abstained on with
`ABSTAIN_AMBIGUOUS`; the fourth (`J1-032`, "What does update() do") was abstained
on as `PARTIAL` before ambiguity was ever tested — correct outcome, different
reason.

The guard also fires on questions that are *not* ambiguous to a human:

| query | resolves to |
|---|---|
| `J1-028` "…the reloader's child process" | `reloader` → **10** symbols |
| (smoke test) `Response` | **6** symbols |
| `J1-003` `utils.py` | **8** artifacts |

At corpus scale a bare name is almost never unique, largely because the corpus
includes `examples/` and `tests/` alongside `src/`. The abstention is correct
under the current rule and is not a defect; it does mean that name-only
addressing stops working as a corpus grows.

## 5. Findings the gate did not ask about, recorded not acted on

**5.1 The graph is mostly unlinked.**

| predicate | resolved to a symbol | unresolved (name literal only) |
|---|---|---|
| `CONTAINS` | 5,962 | 0 |
| `CALLS` | 1,405 | **6,970** |
| `IMPORTS` | 0 | **1,582** |
| `EXTENDS` | 0 | **163** |

Every `EXTENDS` and every `IMPORTS` edge carries a name, not a link. The cause is
visible in `resolver.resolve()`: everything that is not `CALLS` is routed through
`_resolve_import`, which asks whether the target *name* is a module in the
corpus. For `class NotFound(HTTPException)` the backend had already resolved
`HTTPException` deterministically in module scope, and `_resolve_import`
**discards that resolution** and records `UNRESOLVED`.

This is a capability loss, not a correctness failure — no wrong edge is created,
and K-1.1's independent verification of `EXTENDS` precision at 1.000 measured the
claims that are made, not the links that are missing. It is why `J1-052` ("which
status codes have exception classes") is `partial`: 31 `EXTENDS` claims name
`HTTPException` and not one of them points at it.

**Not fixed.** The system is frozen for J-1 and this is not an integrity failure.

**5.2 85 of 225 artifacts are never read.** Four answerable questions (10%) are
about `pyproject.toml` and `CHANGES.rst`. The compiler analyses `.py` only, so
supported Python versions, runtime dependencies, the lint configuration and the
removal of `OrderedMultiDict` are all outside the graph by construction.

**5.3 One gold label the query author disagreed with.** The author believed
`Database` was ambiguous across example apps. The source has exactly one
(`examples/cupoftee/db.py`), so it is labelled `answerable`. Recorded because it
is the reason gold is established from source rather than from anyone's
recollection.

## 6. The fork this analysis exists to resolve

> Can the compiler not represent these facts, or does it hold them and fail to
> retrieve or interpret them?

**It cannot represent them.** For 33 of 42 answerable questions no emitted
predicate expresses the required fact, and for 32 the claim is simply not in the
database. The four `HAS_*` predicates that *are* declared would close at most the
6 parameter-default questions and part of the docstring group.

The 17 behavioural questions are not reachable by adding predicates of this kind
at all. They are the largest group, and they are what developers actually asked.
