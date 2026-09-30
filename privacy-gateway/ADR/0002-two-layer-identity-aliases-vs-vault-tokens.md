# ADR-0002: Two-layer identity — model-facing aliases are not vault tokens

- **Status:** Proposed (Phase 0)
- **Date:** 2026-09-30
- **Supersedes:** none

## Context

The source report conflates two different things under "tokenization":

1. **What the model sees** in place of a sensitive value.
2. **How the original is protected at rest** so it can be restored later.

Sending AES-SIV or FPE ciphertext to the model (the literal reading of the
report) is wrong on every axis: base64 ciphertext costs 20–60 model tokens per
entity, degrades task quality, and — because deterministic encryption under a
wide-scope key is globally stable — makes every prompt linkable to every other
prompt mentioning the same person. Crypto belongs at rest, not in the prompt.

Industry evidence: Uber's GenAI Gateway reports that *inconsistent* placeholders
broke caching and RAG and hurt quality; LiteLLM's Presidio integration resets its
counter per request, so the same person gets a different placeholder each turn.
Consistency within a scope is a functional requirement, not a nicety.

## Decision

**The model sees a short, typed, scope-derived alias. Nothing cryptographic is
ever reversible from the alias alone.**

```
alias      = "[" + TYPE + "_" + crockford32( HMAC-SHA256(k_scope, TYPE ‖ 0x1F ‖ canon(value)) )[:6] + "]"
k_scope    = HKDF-SHA256(ikm = k_tenant_alias, info = "pgw/alias/v1" ‖ scope_id)
scope_id   = conversation_id  (client-supplied, bound to app)   |  random per request (default)
```

Example: `Rahul Sharma` → `[PERSON_7QX2MA]`, `rahul.sharma@acme.in` → `[EMAIL_K3D9PT]`.

Properties this buys:

| Property | How |
|---|---|
| Same value → same alias across turns in one conversation, **with no stored state** | Deterministic HMAC under a per-scope key. The client re-sends history; the gateway re-derives identical aliases. |
| No linkability across conversations/tenants | Scope key differs per conversation; tenant key differs per tenant. |
| Not reversible from the alias | HMAC is one-way; 30 bits of output cannot be inverted even for low-entropy values without the key. Reversal uses the in-request map (ADR-0003). |
| Robust de-tokenisation | The 6-char high-entropy suffix lets the restorer accept mangled forms (`PERSON_7QX2MA`, `person_7qx2ma`, missing brackets) with negligible false-match risk. A sequential `PERSON_1` cannot be matched leniently without false positives. |
| Bounded streaming hold-back | Max alias length is fixed (≤ 31 chars), so the stream restorer's hold-back buffer is bounded (ADR-0006). |
| Fabrication detectable | An alias-shaped string whose suffix is not in the scope map is, by construction, invented by the model → output gate event. |

Collision handling: 30 bits per type; at 200 entities of one type in a single
request, P(collision) ≈ 2×10⁻⁵. A collision is always detectable (both values
are in the request) and is resolved deterministically by extending the suffix
of the lexicographically larger canonical value to 8 chars.

`canon(value)` is type-specific: casefold + NFKC + whitespace-collapse for names;
digits-only for Aadhaar/phone/account numbers; lowercase for email. So
`rahul.sharma@ACME.in` and `rahul.sharma@acme.in` share an alias; `98200 12345`
and `+91-9820012345` share an alias.

**Vault tokens** (only when a durable mapping is required, ADR-0003) are a
separate object: `ciphertext = AES-SIV(k_scope_dek, value, AD=[tenant, scope, TYPE])`,
stored server-side, keyed by `(scope_id, alias)`. They never leave the gateway.

## Open question → experiment, not opinion

No published study measures verbatim round-trip fidelity by placeholder format.
Phase 1 runs a pre-registered experiment (EVALUATION_PLAN.md §E-FMT) comparing
`[PERSON_7QX2MA]`, `<PERSON_1>` and realistic surrogates on the target models.
The alias allocator is an interface, so the loser is a config change, not a
rewrite. If sequential aliases win decisively, they require conversation-scoped
state (ADR-0003 durable store) — that cost is explicit.

## Consequences

**Good.** Stateless consistency; no PII store for the chat path; strong
leak/fabrication detection; bounded streaming latency.

**Bad.** Opaque tags drop gender and cultural cues ("his salary" with
`[PERSON_7QX2MA]` is fine; "write a gender-neutral letter to …" loses
information the task never needed anyway). Name-dependent tasks (salutations
in regional languages) lose quality — measured in E-UTIL, not assumed away.

**Accepted risk.** The alias reveals the entity *type*. That is deliberate:
the type is what lets the model do the task.

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Ciphertext (AES-SIV/FPE) in the prompt | Token cost, quality loss, global linkability. Crypto at rest, not in the prompt. |
| Per-request sequential counters (`<PERSON_1>`) | Inconsistent across turns (Uber's documented failure); lenient matching unsafe. Kept only as an E-FMT candidate. |
| Realistic surrogates by default | Inflection/partial-mention matching is hard, surrogates can collide with real people, and leaked surrogates are indistinguishable from real PII to a human reviewer. Candidate for Phase 3 where utility data justifies it. |
| FPE (FF3-1) for IDs | NIST's SP 800-38G Rev.1 draft removes FF3/FF3-1; FF1 only, and 10–12 digit domains sit near the minimum-domain floor. Use only if a downstream system hard-validates format — none in Phase 1 does. |
