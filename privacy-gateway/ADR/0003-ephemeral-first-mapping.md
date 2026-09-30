# ADR-0003: Ephemeral-first mapping — no PII database on the chat path

- **Status:** Proposed (Phase 0)
- **Date:** 2026-09-30
- **Supersedes:** none

## Context

The report puts an encrypted token vault at the centre of the design. A vault
is a database of every sensitive value that ever passed through the gateway —
the single most valuable target in the system, and a DPDP liability in its own
right (it *is* personal data, pseudonymised or not; the Act has no
pseudonymisation carve-out).

Observation: the OpenAI and Anthropic chat APIs are **stateless**. The client
re-sends the full message history every turn. Every raw value the response can
legitimately refer to is therefore present *in the current request*.

## Decision

**The restore map is built per request, from the request, held in process
memory, and destroyed when the response completes. A durable vault exists only
for the cases that provably need it, behind the same interface.**

```python
class MappingStore(Protocol):
    def bind(self, scope: Scope, alias: str, entity: DetectedEntity) -> None: ...
    def resolve(self, scope: Scope, alias: str) -> Resolved | Unknown: ...
    def close(self, scope: Scope) -> None: ...

EphemeralMappingStore   # Phase 1. dict in request context; dropped on close (best-effort: Python cannot guarantee zeroisation).
DurableMappingStore     # Phase 3. PostgreSQL + envelope encryption. Only for:
                        #   (a) stateful provider APIs (Responses API previous_response_id,
                        #       Assistants threads) where history is NOT re-sent,
                        #   (b) async/batch jobs whose output is restored later,
                        #   (c) RAG corpora tokenised at ingestion (ADR-0008),
                        #   (d) audited re-identification after the fact.
```

Durable store design (Phase 3), fixed now so Phase 1 does not paint it into a corner:

| Column | Notes |
|---|---|
| `tenant_id, scope_id, alias` | PK. |
| `entity_type, policy_id, policy_version` | Why it was tokenised. |
| `ciphertext` | `AES-SIV(k_scope_dek, canon_value, AD=[tenant, scope, type])`. |
| `created_at, expires_at` | Mandatory TTL; default 24 h conversation, 30 d batch. |
| `scope_keys(tenant_id, scope_id, wrapped_dek, kek_version)` | One random DEK per scope, wrapped by the tenant KEK held in KMS/HSM. **Deleting the scope-key row crypto-shreds the whole scope** — DPDP erasure is one `DELETE`. |

Re-identification from the durable store is a privileged operation: separate
service identity, purpose-bound, rate-limited, every call audited with the
alias (never the value).

## Consequences

**Good.** Phase 1 ships with **no persistent PII anywhere in the gateway**.
Breach blast radius of the gateway at rest ≈ zero. Erasure requests are trivial.
Horizontal scaling needs no shared state.

**Bad.** Stateful provider APIs are unsupported in Phase 1 (rejected with a
clear error). A restart mid-stream loses the map — the stream fails closed
rather than returning un-restored aliases silently.

**Accepted risk.** Raw values exist in gateway memory for the request lifetime.
Mitigation: no swap, core dumps disabled, no payload logging, memory not shared
across tenants' requests beyond the process.

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Vault-first (report's design) | Builds the highest-risk component before proving the detector works. Most use cases do not need it. |
| Redis with TTL for all mappings | Still a plaintext-adjacent store of every value; adds a network hop to the hot path for no Phase 1 benefit. |
| Client-held mapping (return encrypted map to the app) | Moves key custody to every app; the app then becomes part of the crypto boundary. |
