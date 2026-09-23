# DEMO 0.1 — evidence-first answers over a real repository

Ask a Python repository a natural developer question. Get an answer where every
statement is linked to the exact source bytes it rests on, shown beside the
deterministic structural facts the compiler derived — or get a refusal that says
why.

**Status: working.** 205 tests pass. The deterministic half runs with no API key.
The semantic half is implemented and tested against a scripted model; it has not
yet run against a live model in this environment (§Known limits).

---

## Run it

```bash
# 1. compile a repository (no model, no key, ~6s for Werkzeug)
python3 -m kgq.cli compile --root eval/corpus3 --db eval/j1/demo.sqlite

# 2. ask — deterministic half only
python3 -m kgq.cli ask "How does send_from_directory prevent unsafe paths?" \
    --db eval/j1/demo.sqlite --root eval/corpus3 --no-llm

# 3. ask with a model
export KGQ_BASE_URL=http://localhost:11434/v1   # or Groq / OpenRouter
export KGQ_MODEL=qwen2.5-coder:7b
# export KGQ_API_KEY=...                        # not needed for a local model
python3 -m kgq.cli ask "How does send_from_directory prevent unsafe paths?" \
    --db eval/j1/demo.sqlite --root eval/corpus3 --html answer.html
```

Exit code is `0` when an answer was shown and `2` when the system abstained, so
it composes in a script.

## What the flagship question produces

Compiled fresh: **225 artifacts, 6,102 symbols, 16,244 claims, 0 absent,
invariants clean.**

**Retrieval — no model involved:**

| span | how it was found | size |
|---|---|---|
| `werkzeug.utils.send_from_directory` | exact identifier | 1,586 B |
| `werkzeug.exceptions.NotFound` | graph: `send_from_directory CALLS NotFound` | 306 B |
| `werkzeug.security.safe_join` | graph: `send_from_directory CALLS safe_join` | 2,531 B |
| `werkzeug.utils.send_file` | graph: `send_from_directory CALLS send_file` | 8,440 B |

**`safe_join` is the answer, and the question never names it.** It was reached by
following a compiler-derived edge — the thing a lexical or embedding search has
no principled way to do.

**Answer (PROPOSED — semantic interpretation):**

> send_from_directory does not trust the path it is given. It passes the
> directory and the untrusted path to safe_join, which returns None when the
> result would escape the base directory; send_from_directory then raises
> NotFound rather than opening the file.

**Statements and their evidence:**

| statement | evidence | verified | structural check |
|---|---|---|---|
| joins the untrusted path with `safe_join`, raises `NotFound` when refused | `1039eea5bf62` · `src/werkzeug/utils.py` bytes 18574–20160 | **yes** | `CALLS(send_from_directory, safe_join)` → **SUPPORTED** |
| `safe_join` returns `None` when the component would escape the base directory | `7e51d079e4aa` · `src/werkzeug/security.py` bytes 4684–7215 | **yes** | — |

Every byte range was re-read from the original file and compared to the stored
quotation. `attempts=1`, `regenerations=0`, context 12,863 chars (~3.2K tokens).

`demo_0_1_answer.html` renders the whole path: answer, each statement with its
evidence, the source excerpts, the `DERIVED` structural facts kept visually
separate from the `PROPOSED` interpretation, and why the outcome was reached.

## What happens when the model misbehaves

The same question, answered by a model that invents a citation and a call edge:

```
ABSTAIN   attempts=2
statement 'It uses a sandbox module.':
  evidence 0000000000: no such evidence id in the store (fabricated citation)
```

Rejected, regenerated once with the reason handed back, rejected again,
abstained. **No sentence was silently dropped** — dropping the bad statement
would have left a shorter answer that looked fully supported.

## Success criteria

| # | criterion | met |
|---|---|---|
| 1 | a natural question with no query grammar | **yes** — no special syntax; 10/10 target questions retrieve evidence |
| 2 | relevant source evidence retrieved | **yes** — identifier, graph hop, BM25 |
| 3 | structured model answer | **yes** — JSON contract, parser rejects free prose |
| 4 | every material statement maps to retrieved evidence | **yes** — enforced, not requested |
| 5 | invalid mapping causes rejection or regeneration | **yes** — reject → regenerate once → abstain |
| 6 | exact evidence displayed | **yes** — file, byte range, excerpt, in text/JSON/HTML |
| 7 | deterministic facts shown separately | **yes** — `DERIVED` vs `PROPOSED`, distinct sections |
| 8 | a structural assertion cannot fabricate facts | **yes** — checked against the graph; a DB trigger makes a model-established structural claim unrepresentable |
| 9 | the system can abstain | **yes** — five distinct abstention reasons |
| 10 | works from a fresh database | **yes** — the demo run compiles from empty each time |

## The ten target questions, deterministic half only (no model)

| id | evidence spans | context | structural facts | top span |
|---|---|---|---|---|
| J1-009 | 6 | 7,167 B | 9 | `werkzeug.security.safe_join` |
| J1-037 | 4 | 13,836 B | 6 | `werkzeug.local.LocalProxy` |
| J1-047 | 5 | 13,747 B | 37 | `werkzeug.utils.send_from_directory` |
| J1-049 | 4 | 21,734 B | 20 | `werkzeug.serving.run_simple` |
| J1-054 | 5 | 9,355 B | 12 | `werkzeug.routing.map.MapAdapter.build` |
| J1-006 | 4 | 5,992 B | 3 | `simplewiki.utils.Response` ⚠ |
| J1-022 | 2 | 3,365 B | 0 | `werkzeug.sansio.response.Response.max_cookie_size` |
| J1-002 | 6 | 18,368 B | 0 | `werkzeug.middleware.proxy_fix.ProxyFix` |
| J1-043 | 6 | 21,317 B | 0 | `werkzeug.middleware.shared_data.SharedDataMiddleware` |
| J1-038 | 6 | 16,555 B | 0 | `werkzeug.middleware.http_proxy.ProxyMiddleware` |

⚠ `J1-006` retrieves an example app's `Response`, not
`werkzeug.sansio.response.Response`, because the question names `Response` — which
matches several classes — and describes `default_status` in prose rather than
naming it. Honest limitation, not fixed by tuning.

## Known limits

**Grounding is not entailment.** The gate proves a citation exists, was
retrieved, and still matches the source bytes. It **cannot** prove those bytes
support the sentence. An answer that cites the right function and describes it
wrongly passes every check. Nothing here is called *verified*; the words used are
*grounded*, *evidence-linked*, *structurally checked where applicable*, and
`PROPOSED`.

**Not run against a live model here.** `api.groq.com` is denied by this
environment's network policy, and no local model is installed. The provider is
~170 lines of stdlib `urllib` against a standard endpoint, and the full path —
contract parsing, validation, rejection, regeneration, abstention — is exercised
by 35 offline tests. But the number that matters, *how often a real model
produces a groundable answer*, is unmeasured.

**Safety is not established.** J-1's 0.000 false support was measured on a system
that answered nothing. This one answers. Every safety property must be
re-measured on a fresh, independently authored query set.

**Ambiguity is unhandled in the semantic path.** `Response` matches six symbols.
The compiler's own `decide()` abstains on that; this path retrieves several and
lets the model choose. That is a real gap.

**Falsification reaches only direct inheritance and containment.** `CALLS` is
open-world, so a missing call edge proves nothing and the system says so.

## Files

| | |
|---|---|
| `kgq/` | the query side: provider, retrieval, contract, validate, answer, cli |
| `tests/test_demo_0_1.py` | 35 offline tests |
| `experiments/demo_0_1_run.py` | the end-to-end run that produced this document |
| `DEMO_0_1_RESULTS.json` | its recorded metrics |
| `demo_0_1_answer.html` | the rendered answer path |
| `DEMO_0_1_ARCHITECTURE.md` | how it works and why |
