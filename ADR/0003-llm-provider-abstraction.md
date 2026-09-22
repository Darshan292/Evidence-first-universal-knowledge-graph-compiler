# ADR-0003: LLM provider abstraction

- **Status:** Accepted (Phase 0)
- **Date:** 2026-09-22

## Context

Four providers must be supported: Ollama (default, local, no key), OpenAI,
Anthropic, Groq. Their structured-output mechanisms differ materially
(PROVIDER_CAPABILITY_MATRIX.md). The obvious move is to adopt a framework that
abstracts providers for us. The obvious move is wrong here: a framework would own
our retry policy, our caching key, our token accounting and our error taxonomy —
precisely the four things this project must control to satisfy its cost and
provenance constraints.

## Decision

**One function-shaped port, four thin adapters, no framework.**

```python
def complete_structured(
    *, provider: str, model: str, system: str, data_channel: str,
    json_schema: dict, schema_version: str, prompt_version: str,
    max_output_tokens: int, timeout_s: float,
) -> StructuredResult        # .obj | .raw | .usage | .outcome
```

Four rules bind every adapter:

### 1. Provider-native structured output is an optimisation, never a guarantee

Where a provider supports strict schema-constrained decoding we use it. We then
**validate the response against the same JSON Schema locally anyway**. A provider
that claims 100% schema adherence still returns truncated output on
`max_tokens`, still refuses, and still occasionally emits a valid-shaped object
with semantically empty content. Validation is not redundant; it is the contract.

### 2. Schema validity is necessary, not sufficient

After schema validation, every claim goes through **evidence verification**: the
`quoted_text` the model returned must appear verbatim in the cited artifact at
the cited locator. Schema-valid, evidence-unsupported output is discarded and
recorded in `llm_ledger` with `outcome='EVIDENCE_FAIL'`. This catches the failure
mode schemas cannot: a well-formed fabrication.

### 3. The data channel is data

Source content is passed in a delimited `data_channel` block with a system
instruction stating that text inside it is material to analyse and that any
instruction found within it must be extracted as content, never executed. No
source text is ever concatenated into the instruction position.

### 4. The cache key is the whole determinism contract

```
sha256(provider ‖ model_id ‖ prompt_version ‖ schema_version ‖ task ‖ content_sha256)
```

Changing a prompt, a schema, or a model invalidates exactly the affected entries
and nothing else. This is what makes re-indexing reproducible and what makes
"tokens avoided" a measurable number rather than a slogan.

## Abstraction ledger

- **Concrete problem now:** four providers, four structured-output dialects, one
  caller that must not care.
- **Existing mechanism considered:** calling SDKs directly at each call site; an
  off-the-shelf LLM framework.
- **Why reuse was insufficient:** direct calls duplicate retry/cache/ledger logic
  four times; a framework takes ownership of the cache key and error taxonomy
  that our cost and reproducibility constraints depend on.
- **Complexity introduced:** one function signature, one result type, four files.
- **Complexity removed:** retry, backoff, budget, caching and ledger accounting
  exist once.
- **What breaks without it:** the usage ledger and the deterministic cache key
  become unenforceable, and C-1/C-3 become promises rather than properties.

## What this deliberately is NOT

No `ProviderFactory`. No `BaseProvider` inheritance hierarchy. No registry. No
streaming (the workload is batch extraction; streaming adds partial-parse
failure modes for no benefit). No tool-calling loop — extraction is one request
in, one validated object out. No agent framework.

## Failure policy

| Condition | Response |
|---|---|
| Schema violation | retry ≤ 2 with the validation error appended; then `SCHEMA_FAIL`, no claim |
| Evidence unverifiable | **no retry**, discard, `EVIDENCE_FAIL` |
| Rate limit (429) | bounded exponential backoff, honouring `retry-after`; counts against job budget |
| Repeated provider failure | circuit breaker opens; job continues in deterministic-only mode |
| Timeout | one retry at the same budget, then abandon the work item as `FAILED` |
| Budget exhausted | job stops issuing calls and completes deterministically; ledger records the stop |

A provider failure must never fail a compile. It reduces the semantic layer's
coverage, which is recorded and reported, never hidden.

## Consequences

**Good.** Ollama-only operation is the default and needs no key. Provider choice
is a one-line change. Spend is attributable per claim. Adding a fifth provider is
one file.

**Bad.** We do not get framework features (streaming, agents, tool loops) for
free. We do not want them.

**Accepted risk.** Provider APIs drift. Mitigation: adapters are thin enough to
re-verify quickly, each adapter has a live contract test that is allowed to fail
loudly in CI without blocking the deterministic suite, and pinned SDK versions.
