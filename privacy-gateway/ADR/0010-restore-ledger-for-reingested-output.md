# ADR-0010: Restore ledger — restored values must be caught when the client re-sends them

- **Status:** Proposed (Phase 0 review)
- **Date:** 2026-09-30
- **Amends:** ADR-0003 (adds keyed-hash state), ADR-0005 (adds detector D3b)

## Context

ADR-0003 relies on stateless chat APIs: the client re-sends history, and the
gateway re-detects and re-derives identical aliases. That holds only while the
**original** occurrence of each value is still in the re-sent history.

Clients truncate history. Every production chat app trims old turns to stay inside the
context window. Consider:

```
turn 1  user:      "Summarise Rahul Sharma's grievance ..."   ← NER detects PERSON
        provider sees  [PERSON_7QX2MA]
        client gets    "Rahul Sharma raised ..."               ← gateway restored it
turn 6  client drops turn 1's user message; re-sends turn 1's assistant message
        "Rahul Sharma raised ..."                              ← only occurrence left
```

At turn 6 the name is protected only if a detector finds it again in a
different sentence. The detection cache (ADR-0005) does not close this gap. It is an
in-process LRU with sticky routing that DESIGN §6 describes as "for the cache hit rate,
not correctness". Also, the client's re-serialised text need not be byte-identical.
For names found only by NER (≈30 % of names by the E-DET construction), a miss
here sends the provider a raw value **that it had previously only ever seen as an
alias**. That is the worst kind of leak for this product. The gateway itself put the
value into the client's hands, and the gateway then ships it out.

This is not T1 (D5 propagation only spans one request) and not T2 (a first-time
detector miss). It is a new threat, T21.

## Decision

**Every value the restorer emits is recorded, as a keyed hash only, in a
per-conversation restore ledger. Every later request in that conversation is
matched against the ledger before any other detector runs.**

```
on restore(scope, alias → value, TYPE):
    R[tenant, app, conversation_id] += { HMAC(k_tenant_rl, TYPE ‖ 0x1F ‖ canon(value)) → TYPE }

detector D3b (runs on assistant-role messages and tool-call arguments):
    for each candidate span (token 1–4-grams; D2 regex candidates for EMAIL/PHONE/IDs):
        h = HMAC(k_tenant_rl, TYPE ‖ 0x1F ‖ canon(span))  for each TYPE in R
        hit → entity(TYPE, source=RESTORE_LEDGER, score=1.0)
    D5 then propagates every hit to every other occurrence in the request.
```

Rules:

1. **Conversation id is mandatory for multi-turn restore.** An app with
   `restore_output: true` that sends any `assistant`-role message without
   `X-PGW-Conversation-Id` gets `400 PGW_CONVERSATION_ID_REQUIRED`. Without an id
   the gateway cannot know which ledger applies. Assistant messages in a request are
   themselves the proof that the request is multi-turn.
2. **Hashes only.** The ledger holds `HMAC → TYPE`, no plaintext and no alias.
   This is the same class of data as the EDM dictionary (ADR-0005 D3), and it
   is still pseudonymous personal data. TTL equals the conversation TTL (default 24 h), and
   the ledger is covered by the erasure path (delete by conversation id).
3. **Shared, not in-process.** Correctness must survive pod restarts and routing
   misses. Phase 1 stores it in the PostgreSQL instance that already holds the
   egress ledger: one table, `(tenant, app, conversation_id, h) → type, expires_at`.
4. **Fail closed.** Ledger unreachable → treated as a detector failure under the
   ADR-0007 matrix (block for RESTRICTED/CONFIDENTIAL).
5. **The egress invariant covers ledger hits automatically.** A ledger hit yields a
   plaintext span inside the current request. The span joins the entity set and so
   enters the Aho-Corasick sweep.

## Consequences

**Good.** Closes T21 deterministically, with no dependence on NER recall. It
also strengthens multi-turn detection in general: a name that the gateway
restored once gets caught with score 1.0 for the rest of the conversation.

**Bad.**
- Phase 1 now has shared state on the hot path: one indexed lookup per request,
  plus writes after each restoring response. Measured in E-PERF.
- The candidate generation cost is O(assistant text) HMACs per turn, roughly 1 µs each. Scanning only
  assistant/tool-call text keeps this linear. The gate is that D3b adds ≤ 10 ms p95 at 32 k
  tokens of history.
- It is one more store to secure and erase. Its contents are keyed hashes, so a DB
  dump without `k_tenant_rl` yields nothing directly. With the key, low-entropy
  values can be confirmed (same residual as T17).

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Rely on the detection cache | Designed as an optimisation. Misses on restart, reroute, or any byte change in the client's re-serialisation. |
| Durable alias→value map for all chat | Makes a plaintext vault the core of the design, which ADR-0003 rejected for good reason. |
| Require clients never to truncate | Unenforceable, and it contradicts how every chat SDK manages context. |
| Signed sidecar returned to the client (map or hashes in a header/field) | Breaks OpenAI-SDK compatibility, makes the app part of the crypto boundary, and apps drop unknown fields. |
| Don't restore at all | Kills the primary use case (drafting to a named person). |

## Verification

- **E-STREAM-T21** (new, in EVALUATION_PLAN): multi-turn scripts where the
  originating user turn is dropped before turn *n*, with NER forced to miss (mock
  detector) and the in-process cache cleared between turns. **Gate: 0 raw values
  in egress.** Run across two gateway pods to prove it doesn't depend on sticky routing.
- E-CANARY extended: the restore-ledger table is a sink. It must contain no canary
  plaintext.
