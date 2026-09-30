# Roadmap — phases with exit gates

- **Status:** Proposed (Phase 0)
- **Date:** 2026-09-30

Each phase ends at a gate. A phase that misses its gate does not proceed; it
produces a report saying why. Durations assume **2 engineers**; with 1, roughly
double them. They are estimates, not commitments.

| Phase | Scope | Exit gate | Est. |
|---|---|---|---|
| **0 — Design & preconditions** | This document set. Name a use-case owner and one pilot app. Get infosec + privacy sign-off on the trust boundary and the provider list. Build the E-DET gold corpus v0 (≥ 500 items, dev split only — seeds generators; the full powered corpus per EVALUATION_PLAN is required before the Phase 1 gate). | Signed-off design; one app owner committed; gold v0 frozen. | 2–3 wks |
| **1 — Deterministic core** | Proxy for OpenAI chat + Anthropic messages (stream + non-stream); D0–D3, D5; policy engine + simulator; HMAC aliases; ephemeral mapping; restore ledger (ADR-0010); streaming restorer; output gate (deterministic); egress invariant + ledger; PII-free logging; canary suite. **No NER.** | E-STREAM, E-STREAM-T21, E-EGRESS-FUZZ, E-CANARY pass; E-DET gates pass for HIGH/EMAIL/PHONE/EMPLOYEE_ID/COMPENSATION; PERSON recall *reported*; E-FMT run and ADR-0002 closed; E-PERF N-1 (non-NER) met. Pilot app live on synthetic data. | 6–8 wks |
| **2 — Contextual detection** | GLiNER (ONNX) pool, fusion + per-type thresholds, detection cache, fuzzy entity linking (add-only), E-ADV suite, quasi-identifier risk scoring spike. | E-DET PERSON ≥ 0.93 recall; E-ADV gates; E-PERF with NER; E-UTIL ≤ 5 % degradation for pilot purposes. | 5–6 wks |
| **3 — Durable mapping & keys** | KMS integration, key hierarchy, durable mapping store with per-scope DEKs + crypto-shred, audited re-identification API, stateful provider API support, key rotation runbook. | Pen test of re-identification path; crypto review; erasure demo (scope delete → unrecoverable). | 4–5 wks |
| **4 — Enterprise sources & RAG** | EDM dictionary sync from HRIS (Workday/UKG via generic connector interface), ACL import, RAG ingest through `pgw-core`, local embeddings, ACL-filtered retrieval. | RAG leakage tests (T8) pass; EDM sync with HMAC-only storage verified. | 6–8 wks |
| **5 — Agents & governance** | Tool gateway (allow-lists, arg schemas, user-identity propagation, approval for writes), governance console (no values), OPA/Cedar evaluation for relationship-based authorisation. | OWASP LLM06 scenarios pass; console reviewed by privacy office. | 6–8 wks |

## What is deliberately not in Phase 1

Vault, KMS, GLiNER, RAG, agents, console, confidential computing, FPE,
surrogates, multimodal (OCR/audio). Each is either unproven-necessary or
depends on Phase 1 evidence. Building them first is the most likely way this
project fails (RISK_REGISTER R-01).

## Phase 1 build order (inside the phase)

1. `ir` + OpenAI adapter + pass-through proxy with the egress invariant wired
   in **from the first commit** (the invariant is the test oracle for
   everything after it).
2. D2 recognisers (Indian IDs, secrets) + policy engine + simulator.
3. Alias allocator + ephemeral mapping + non-streaming restore + restore ledger (ADR-0010).
4. Streaming restorer + E-STREAM golden suite.
5. D1 structure awareness + D3 EDM + D5 propagation.
6. Output gate, ledger, canary suite, E-PERF.
7. Anthropic adapter.
