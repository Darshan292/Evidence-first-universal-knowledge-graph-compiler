# J-2 PRE-FLIGHT — verification before the restarted Run 1

Run 2026-09-24, immediately before Run 1 was launched.

## Frozen artefacts re-verified by SHA-256

| Artefact | Expected | Actual | |
|---|---|---|---|
| `eval/j2/J2_QUERIES.json` | `977ca076…6b1a713` | `977ca076…6b1a713` | **PASS** |
| `eval/j2/J2_GOLD.json` | `4e6b39b3…cb873a` | `4e6b39b3…cb873a` | **PASS** |
| `eval/corpus3` content hash | `19f4028a…32d35f` | `19f4028a…32d35f` | **PASS** |

Neither queries nor gold were regenerated or modified.

## System under test

    compiler commit   7bc625319922393bd62313c2a47b845d1aa94616
    schema version    1
    corpus commit     6048fa48753c7b61e35cc34537667809dee8fa35   (required commit: match)

### Semantic-system drift since the freeze

`git diff 7bc6253 HEAD -- kgc/ kgq/` returns exactly one file:

    kgq/provider.py | 11 ++++++++++-

and the whole of that diff is the DEFECT-001 User-Agent header, its explanatory
comment, and the import it needs. Nothing else. Working tree clean under `kgc/`
and `kgq/`.

**Unchanged:** answer validator, evidence validator, deterministic structural
checks, identity rules, retrieval, claim semantics, prompts, budgets.

## Prior run discarded

The previous Run 1 attempt was stopped at 10/48 and had persisted **nothing** —
the old runner wrote its output only at the end. No partial result from it is
used anywhere. The new runner persists after every question.

## §4 — prompt caching: probed, not induced

The same large prompt was sent twice, 35 s apart, with no prompt change:

    call-1  usage keys: completion_time, completion_tokens, completion_tokens_details,
                        prompt_time, prompt_tokens, queue_time, total_time, total_tokens
            prompt=3175  completion=8  total=3183
    call-2  identical keys
            prompt=3175  completion=8  total=3183

**Groq exposes no `prompt_tokens_details` and no `cached_tokens` for
`openai/gpt-oss-120b` on this tier, and an identical prompt is billed
identically.** No prompt caching is observable. The runner still records
`cached_tokens` and `cache_hit` per request, so if the provider ever starts
reporting them the data is there; nothing was done to the prompts to induce it.

Incidentally observed and now recorded per request:
`completion_tokens_details.reasoning_tokens`. `gpt-oss-120b` is a reasoning
model and spends a large share of its output budget before emitting any answer
text — in the harness probe, 1041 of 1200 output tokens on one call. That is
measured, not adjusted for.

## Measurement correctness

Per-request `input_tokens`, `output_tokens`, `total_tokens`, `cached_tokens`,
`cache_hit`, `reasoning_tokens`, HTTP status and API seconds are read **off the
wire**, from the provider's own `usage` block, by wrapping
`urllib.request.urlopen` inside the runner process. Nothing is estimated from
character counts, and `kgq/provider.py` stays byte-identical to the
DEFECT-001 state.

## Pacing preserved

`PacedProvider` unchanged in intent: 8,000 TPM ceiling, 0.92 headroom, no
concurrency, exponential backoff on 429. One run at a time — Run 2 starts only
after Run 1 has completed and persisted.
