# ADR-0009: Tool calls and RAG are enforcement points, not afterthoughts

- **Status:** Proposed (Phase 0; implementation Phase 4–5)
- **Date:** 2026-09-30
- **Supersedes:** none

## Context

AWS documents that Bedrock Guardrails' sensitive-info filter does not cover
tool-use fields; Salesforce disables masking for agents. In HRIS work the tool
result *is* the payload: `get_worker(EMP_…)` returns a JSON record full of PII
straight into the next model turn. Prompt-only protection is theatre for agents.
Likewise, RAG that retrieves raw HR documents into context bypasses
prompt-side masking entirely.

## Decision

### Tools (via the proxy, Phase 1 — already free)

In the OpenAI/Anthropic protocols, tool results come back as `tool` /
`tool_result` messages in the next request. The gateway treats them as input:
D1 structure-aware detection on the JSON, same scope, same aliases. Tool-call
arguments emitted by the model are restored for the *calling app* (inside the
boundary) only if the app is registered with `restore_output: true`.

### Tool gateway (Phase 5)

For agents that call enterprise APIs (Workday, UKG, ERP), the model never holds
credentials and never calls directly:

```
model ─► tool_call(name, args with aliases) ─► gateway: resolve aliases in-boundary
      ─► tool policy: allow-list per (app, purpose), arg schema validation,
         caller identity propagation (the *user's* HRIS permissions, not a service account's),
         rate limit, human-approval flag for write actions
      ─► enterprise API ─► result ─► detect/tokenise ─► back to model
```

Write actions (`update_*`, `terminate_*`, `approve_*`) require explicit
human approval by default — OWASP LLM06 Excessive Agency.

### RAG (Phase 4)

1. **Ingest**: documents pass through `pgw-core` before chunking/embedding.
   Chunks are stored *tokenised* with a corpus scope; the durable mapping store
   (ADR-0003) holds reversals. Embeddings are computed on tokenised text by a
   **local** embedding model. (Embeddings of raw text are not anonymous.)
2. **ACL metadata** copied from the source system per chunk; retrieval filters
   by the *requesting user's* entitlements before ranking.
3. **Context assembly** passes retrieved chunks through the gateway again
   (defence in depth; catches ingest-time misses with newer detectors).

## Consequences

The proxy path covers tool *results* from day one at zero extra design cost.
The tool gateway and RAG ingest are real subsystems and are scheduled, not
promised in the MVP.
