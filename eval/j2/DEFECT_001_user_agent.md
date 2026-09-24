# DEFECT-001 — the provider is blocked by a real provider's edge, and J-2 found it

Recorded **before** any modification, as J-2 §1 requires. Discovered during the
first J-2 run, which is **void** as a result.

## What happened

All 48 questions abstained in ~0.33 s each with `llm_calls: 0`:

```
model unavailable: https://api.groq.com/openai/v1: HTTP 403 b'error code: 1010\n'
```

Cloudflare error 1010 is a block based on the client's signature.

## Root cause — reproduced four ways

`kgq/provider.py` builds its request with `urllib.request.Request` and never
sets a `User-Agent`, so it sends `Python-urllib/3.11`. Groq's edge rejects that.

| Client | User-Agent | Result |
|---|---|---|
| curl | `curl/8.5.0` (default) | **200** |
| curl | `Python-urllib/3.11` (forced) | **403** |
| python urllib | `Python-urllib/3.11` (default) | **403** |
| python urllib | any normal UA | **200** |

The variable is the User-Agent alone. The key, the URL, the network policy and
the payload are identical across all four.

## Why the existing tests did not catch it

`tests/test_provider_transport.py` drives the real `Provider` over real HTTP
against a local `http.server`. A local server has no CDN and accepts any
User-Agent, so the request shape that a real provider's edge rejects passes
there. Eleven passing transport tests proved the protocol and proved nothing
about reachability.

`DEMO_0_2_ARCHITECTURE.md` §11 called the transport "proven against a real HTTP
server". That was true and misleading: it implied reachability it never tested.
The wording is corrected.

## Severity

**This breaks the product for real users**, not just the evaluation. Anyone
following `DEMO_0_2.md` — set `KGQ_BASE_URL` to Groq, set `KGQ_MODEL`, ask a
question — gets a silent abstention with "model unavailable". The workbench
degrades correctly (deterministic evidence is still shown, nothing is
fabricated), but the semantic layer is unreachable.

## Fix

Send an explicit, honest `User-Agent` identifying the client. One line in
`kgq/provider.py`, plus a regression test asserting the header is present.

This is not model-specific tuning and not a change to the evaluation: it does
not touch the validator, the evidence verifier, the structural checks, identity,
retrieval or claim semantics. The frozen query set and gold are untouched.

## Consequence for J-2

Run 1 measured a transport failure, not a semantic layer. It is discarded, not
reported. J-2 restarts from a fresh database after the fix, against the same
frozen queries and the same frozen gold.
