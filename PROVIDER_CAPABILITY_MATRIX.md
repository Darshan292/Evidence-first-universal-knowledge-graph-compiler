# PROVIDER CAPABILITY MATRIX

**Verified:** 2026-09-22. Sources cited per row. Unverified rows are marked
`UNCERTAIN` with the experiment that would resolve them.

> Everything here is treated as an *optimisation hint*. Regardless of what a
> provider guarantees, every response is validated against our JSON Schema and
> every claim is evidence-verified locally (ADR-0003 rules 1–2).

---

## 1. Summary

| | **Ollama** | **OpenAI** | **Anthropic** | **Groq** |
|---|---|---|---|---|
| Role | **default, required** | optional | optional | optional |
| API key needed | **no** | yes | yes | yes |
| Runs fully offline | **yes** | no | no | no |
| Data leaves machine | **no** | yes | yes | yes |
| Structured output | JSON Schema via `format` | Structured Outputs | `output_config.format` / `strict` tools | `json_schema`, `strict` on some models |
| Constrained decoding | yes (schema-guided) | yes (strict mode) | yes | **only** `openai/gpt-oss-20b`/`-120b` |
| Schema adherence guarantee | high, not absolute | high in strict mode | high | model-dependent |
| Cost | $0 | metered | metered | metered |
| Rate limits | local hardware | RPM/TPM tiers | RPM/TPM tiers | RPM/TPM tiers |

---

## 2. Ollama — the required path

- **Structured output:** the `format` parameter accepts either `"json"` or a
  full JSON Schema object; the model is constrained to match it. Pydantic
  `model_json_schema()` output can be passed directly.
- **Why it is the default:** satisfies C-1, C-2, C-3 simultaneously. No key, no
  egress, no cost.
- **Caveat we will not paper over:** schema-guided decoding constrains *shape*,
  not *truth*. A 7–8B local model will produce well-formed, confidently wrong
  extractions more often than a frontier model. This is precisely why evidence
  verification is mandatory and why the deterministic layer carries the load.
- `UNCERTAIN` (U-2): which local model is the best default for rationale
  extraction. **Experiment:** run E-2 against three candidate local models and
  compare evidence-verification pass rate and claim-status accuracy. Decide on
  measurement, not reputation.

*Source: Ollama structured-outputs documentation.*

## 3. OpenAI

- **Structured Outputs** with `response_format` / `json_schema` and `strict: true`.
- Strict mode requires all fields `required` and `additionalProperties: false` —
  our schemas are authored to those constraints so the same schema works
  everywhere.

*Source: OpenAI structured-outputs documentation.*

## 4. Anthropic

- **Structured outputs:** `output_config: {format: {...}}` on `messages.create()`.
  The older `output_format` parameter is deprecated. SDK helper
  `client.messages.parse()` validates responses against the schema.
- **Strict tool use:** `strict: true` as a **top-level field on the tool
  definition** (not on `tool_choice`); requires `additionalProperties: false`
  plus `required`.
- **Citations:** document blocks support `citations: {enabled: true}`, returning
  `char_location` (text) or `page_location` (PDF, 1-indexed) per cited span.
  This is a genuinely useful provenance feature — but note the constraint below.
- ⚠ **Citations are incompatible with `output_config.format`** — combining them
  returns a 400. So a single call cannot both enforce our extraction schema and
  use native citations. **Decision:** we use structured output and our own
  evidence verification, which works identically across all four providers and
  does not depend on any provider's citation implementation. Native citations
  are not adopted.
- Current model IDs (2026-09-22): `claude-opus-5` ($5/$25 per MTok),
  `claude-sonnet-5` ($2/$10), `claude-haiku-4-5` ($1/$5).

*Source: bundled Anthropic API reference, cached 2026-06-24.*

## 5. Groq

- **Structured Outputs** via `response_format: {"type": "json_schema", "json_schema": {...}}`.
- **Two modes, and the distinction matters:**
  - `strict: true` → constrained decoding, guaranteed schema match, **but only
    supported on `openai/gpt-oss-20b` and `openai/gpt-oss-120b`**.
  - non-strict → broader model support, optional fields allowed, **can emit
    malformed JSON or fail validation**.
- Selecting Groq with a non-strict model therefore *increases* our local
  validation and retry load. The adapter records which mode was used per call in
  `llm_ledger` so this is visible rather than surprising.

*Source: Groq structured-outputs documentation.*

---

## 6. What the adapters must normalise

| Concern | Normalisation |
|---|---|
| Schema dialect | one JSON Schema authored to the strictest common subset (all fields required, `additionalProperties: false`) |
| Refusals | mapped to `outcome='REFUSED'`; never retried as a schema error |
| Truncation | `max_tokens` stop → `SCHEMA_FAIL`, never a partial-object parse |
| Token accounting | normalised into `llm_ledger.prompt_tokens` / `completion_tokens` |
| Rate limits | `retry-after` honoured where supplied; bounded backoff otherwise |
| Errors | one taxonomy: `OK, SCHEMA_FAIL, EVIDENCE_FAIL, RATE_LIMIT, TIMEOUT, REFUSED` |

## 7. Selection policy

1. Default **Ollama**. No configuration required.
2. A cloud provider is used **only** when explicitly named by the user.
3. The effective provider is printed at job start and hashed into
   `processing_run.config_hash`.
4. Provider identity, model id, prompt version and schema version are part of
   every cache key — switching providers never silently reuses another
   provider's cached output.
5. No provider is ever required for a compile to succeed.

## 8. Contract tests (A-19)

One task, one schema, run against all four adapters. Each must return either a
schema-valid, evidence-verified object or a cleanly recorded failure. These tests
require network and keys, so they run in a separate suite that is allowed to fail
without blocking the deterministic suite — a broken cloud adapter must never
block a local build.
