# ESU Privacy Gateway — System Design

- **Status:** Proposed (Phase 0 — design, no code)
- **Date:** 2026-09-30
- **Inputs:** `ESU_Privacy_Preserving_LLM_Security_Gateway_Report.md` (survey),
  `research/*.md` (verification of that survey), ADR-0001…0010.

---

## 1. What this system is — in one paragraph

A self-hosted, OpenAI-compatible gateway that is the **only** network path from
ESU applications to LLM providers. For every request it detects sensitive
HRIS/ERP data, replaces it with short scoped aliases according to a
purpose-bound policy, proves the outbound bytes contain none of what it
detected, forwards the request, gates the (streamed) response, and restores
aliases for authorised callers. Phase 1 stores **no personal data at rest**.

What it is **not**: an anonymiser (pseudonymised data is still personal data
under DPDP), a guarantee (detection has measurable recall < 100 %), a
solution to quasi-identifier re-identification (§9), or a replacement for
contracts with LLM providers.

## 2. Requirements

### 2.1 Functional

| ID | Requirement | Phase |
|---|---|---|
| F-1 | Proxy OpenAI Chat Completions (stream + non-stream) and Anthropic Messages. Everything else rejected. | 1 |
| F-2 | Detect the Phase 1 entity set (§5.2) in message text, system prompts, tool results and structured (JSON/XML) content. | 1 |
| F-3 | Apply per-(tenant, app, purpose, destination) policy: TOKENIZE, REDACT, GENERALIZE, ALLOW, BLOCK_REQUEST, DENY_ROUTE_LOCAL. | 1 |
| F-4 | Consistent aliases within a conversation scope with no stored state. | 1 |
| F-5 | Incremental streaming restore; tool-call argument restore. | 1 |
| F-6 | Output gate: fabricated aliases, deterministic PII, EDM hits. | 1 |
| F-7 | Egress invariant + egress ledger. | 1 |
| F-8 | Policy simulator CLI and policy test runner. | 1 |
| F-9 | GLiNER NER + fusion + detection cache. | 2 |
| F-10 | Durable mapping store with crypto-shredding; audited re-identification API. | 3 |
| F-11 | EDM dictionary sync from HRIS; connectors (Workday/UKG/ERP) as *sources of dictionaries and ACLs*, not as data pipes. | 4 |
| F-12 | RAG ingest pipeline + ACL-aware retrieval. | 4 |
| F-13 | Tool gateway for agents. | 5 |
| F-14 | Governance console (counts, types, decisions — never values). | 5 |

### 2.2 Non-functional (targets; each is re-baselined by measurement in Phase 1)

| ID | Target |
|---|---|
| N-1 Latency | Added p95 ≤ 50 ms (D0–D3, ≤ 8 KB new content); ≤ 250 ms with NER on CPU for ≤ 2 KB new content; TTFT overhead in streaming ≤ 50 ms p95. |
| N-2 Throughput | 50 RPS sustained per 4-vCPU gateway pod without NER; NER pool sized separately. |
| N-3 Availability | 99.9 % for the gateway itself. Fail closed means gateway downtime = AI downtime; this is agreed with app owners, not hidden. |
| N-4 Recall | Pre-registered per-type gates (EVALUATION_PLAN.md). Headline: ≥ 0.98 recall on HIGH-risk structured types, ≥ 0.90 on PERSON in HR free text, measured on held-out data. |
| N-5 Leak-by-bug | Zero egress-invariant violations in canary + fuzz suites; any production hit is P1. |
| N-6 At-rest PII (Phase 1) | None. Verified by canary sweep of every sink. |
| N-7 Auditability | Every upstream call has a ledger row with policy/detector versions. |
| N-8 Portability | Runs on Kubernetes or a single VM (docker compose); no cloud-specific managed service required except KMS in Phase 3. |

## 3. Context and trust boundary

```
                  ┌────────────────────────── ESU TRUST BOUNDARY ───────────────────────────┐
  HR assistant ──►│                                                                          │
  Integration  ──►│  ┌──────────── PRIVACY GATEWAY (only holder of provider keys) ────────┐ │
   tooling        │  │                                                                     │ │
  RAG app (P4) ──►│  │ ingress: mTLS/app-key → app registry → purpose check (ADR-0004)     │ │
  Agents (P5)  ──►│  │   │                                                                 │ │──► Azure OpenAI (India region)
                  │  │   ▼                                                                 │ │──► Anthropic / Bedrock
                  │  │ pgw-core:  normalise → detect cascade → resolve → decide → alias    │ │──► local vLLM (in-boundary)
                  │  │   │         (ADR-0005)              (ADR-0006) (ADR-0002/0003)       │ │
                  │  │   ▼                                                                 │ │
                  │  │ egress: serialise → EGRESS INVARIANT (ADR-0007) → ledger → send     │ │
                  │  │   ◄── stream: parse → OUTPUT GATE → RESTORER (ADR-0008) → client    │ │
                  │  └─────────────────────────────────────────────────────────────────────┘ │
                  │   NER worker pool (P2)   policy packs (signed)   EDM dictionary (HMAC)   │
                  │   ledger DB (no content)  mapping store (P3, encrypted)  KMS (P3)        │
                  └──────────────────────────────────────────────────────────────────────────┘
  Egress network policy: only gateway pods may reach provider hostnames.
```

## 4. Request lifecycle (non-streaming; streaming differs only at step 9)

1. **Authenticate** app (mTLS client cert + API key). Resolve registration.
2. **Authorise purpose** (`X-PGW-Purpose`) and destination model. Else 403.
3. **Parse** the provider body into the message IR. Unknown fields in content
   positions → 400 (never forwarded un-inspected).
4. **Normalise** each content segment (D0) keeping an offset map.
5. **Detect** (D1–D5) using the detection cache for previously-seen messages. Assistant-role messages are first matched against the conversation's restore ledger (D3b, ADR-0010).
6. **Resolve** entities (canonical grouping); **decide** action per entity.
7. If any BLOCK_REQUEST / DENY_ROUTE_LOCAL → 422 with types and offsets only.
8. **Transform**: aliases (HMAC under scope key), redaction, generalisation.
   Bind aliases in the ephemeral mapping store.
9. **Serialise** provider body; **egress invariant** sweep; write ledger; send.
10. **Output gate** on aliased response; **restore** if app+policy allow. Each restored value is recorded as a keyed hash in the restore ledger (ADR-0010).
11. Drop the mapping (best-effort zeroisation; see THREAT_MODEL T16); return.

## 5. Components

### 5.1 Module map (`pgw-core` is a pure library; the rest is the service)

| Module | Responsibility | Depends on |
|---|---|---|
| `ir` | Provider-neutral message IR; adapters for OpenAI chat, Anthropic messages | — |
| `normalize` | D0 + offset maps | — |
| `detect` | Detector protocol, D1–D5, fusion, cache | `normalize`, Presidio, GLiNER (P2) |
| `entities` | Canonicalisation per type, resolution | — |
| `policy` | Pack loader, signature check, compiler, `decide()` | — |
| `alias` | HMAC alias allocator, collision handling, grammar | `cryptography` |
| `mapping` | `MappingStore` ephemeral (P1) / durable (P3) | `alias` |
| `egress` | Invariant sweep, ledger writer | `pyahocorasick` |
| `stream` | SSE parsers, restorer, output gate | `alias`, `detect` |
| `gateway` | ASGI app, authn, registry, routing, provider clients | all of the above |
| `sim` | Policy simulator + policy test runner CLI | `pgw-core` |

Stack: Python 3.12, Starlette/uvicorn (async), httpx (HTTP/2 to providers),
Presidio analyzer (pinned), GLiNER via ONNX Runtime (P2), `cryptography`
(HMAC, HKDF, AES-SIV), `pyahocorasick`, PostgreSQL (ledger; mapping in P3),
OpenTelemetry. Rationale for Python: the detection ecosystem is Python; the
proxy is I/O-bound. If the data plane ever needs Envoy-class throughput, the
split is `pgw-core` behind an ext_proc gRPC service (ADR-0001).

### 5.2 Phase 1 entity taxonomy

| Type | Risk | Primary detector | Default action (hris-default pack) |
|---|---|---|---|
| AADHAAR, AADHAAR_VID | HIGH | D2 regex + Verhoeff + context (Verhoeff alone passes ~10 % of random 12-digit numbers) | BLOCK_REQUEST |
| PAN, GSTIN | HIGH | D2 regex + checksum (GSTIN) | BLOCK_REQUEST / TOKENIZE (GSTIN is a business id) |
| BANK_ACCOUNT, IFSC, CARD, UPI_ID | HIGH | D2 (+ keyword for account) | BLOCK_REQUEST (IFSC: ALLOW — bank branch, not person) |
| SECRET (API keys, JWT, passwords, auth headers) | HIGH | D2 | BLOCK_REQUEST, never restorable |
| PASSPORT, VOTER_ID, DRIVING_LICENCE, UAN, ESIC | HIGH | D2 keyword-gated | BLOCK_REQUEST |
| PERSON | MED | D3 EDM, D4 NER (P2), D1 field names | TOKENIZE |
| EMAIL, PHONE | MED | D2 | TOKENIZE |
| EMPLOYEE_ID, CUSTOMER_ID, VENDOR_ID | MED | D2 tenant formats, D3 | TOKENIZE |
| ADDRESS, LOCATION(fine) | MED | D4, D1 | TOKENIZE |
| COMPENSATION | MED | D2 INR patterns + context, D1 field names | TOKENIZE / GENERALIZE by purpose |
| DATE_OF_BIRTH | MED | D2 date + context | GENERALIZE(age decade) |
| HEALTH/LEAVE_REASON, PERFORMANCE_RATING | MED (special) | D1 field names; D4 (P2) | TOKENIZE; free-text health details → REDACT |
| ORG, JOB_TITLE, GRADE | LOW | D4, D1 | ALLOW by default (quasi-identifier caveat, §9) |

Why HIGH types BLOCK rather than TOKENIZE by default: there is no HR drafting or
summarisation task in the Phase 1 purpose list that needs an Aadhaar or bank
number *even as a token*. Blocking tells the app team their data minimisation
is wrong upstream — which is the correct fix.

### 5.3 Key hierarchy

```
KMS / HSM (P3)  or  sealed secret (P1)
  └─ tenant root (per tenant)
       ├─ k_tenant_alias   → HKDF(scope_id) → k_scope        (alias derivation, ADR-0002)
       ├─ k_tenant_edm     → HMAC for exact-data dictionary   (ADR-0005 D3)
       ├─ k_tenant_cache   → HMAC detection-cache keys
       ├─ k_tenant_kek     → wraps per-scope DEKs (AES-SIV)   (ADR-0003, P3)
       └─ k_sign_policy    → verifies policy pack signatures (ops-held)
```
Rotation: alias/EDM/cache keys rotate with a version prefix; rotation breaks
cross-rotation alias consistency by design (bounded linkability window).

## 6. Deployment

- Phase 1: 2+ gateway pods (stateless) behind an internal LB with sticky routing
  on `X-PGW-Conversation-Id` (for the detection cache hit rate, not correctness);
  PostgreSQL for the ledger. Kubernetes NetworkPolicy/egress firewall so only
  gateway pods reach provider endpoints.
- Phase 2: NER pool as a separate deployment (CPU, int8 ONNX; GPU if p95 needs it).
- Hardening: read-only root FS, no shell in image, core dumps off, no swap,
  distroless base, SBOM + pinned hashes (`pip --require-hashes`), signed images.
- Provider choice: prefer providers with contractual zero data retention and
  in-India or approved-region processing; recorded per destination in the app
  registry. Zero retention is contractual, not technical — the gateway exists
  precisely because we do not rely on it alone.

## 7. Observability (what we measure, never what we saw)

Metrics: requests by (app, purpose, decision); entities by type and action;
blocks by type; egress violations (must be 0); output-gate events by kind;
fabricated-alias rate; detector latency by stage; cache hit rate; TTFT overhead.
All logs from the allow-listed schema (ADR-0007).

## 8. Multi-tenancy

ESU serves multiple client organisations. Isolation is by key (per-tenant root),
by policy (per-tenant packs), by EDM dictionary, by provider credentials and
by ledger partition. No cross-tenant cache. A single process may serve several
tenants in Phase 1; dedicated pods per tenant are a deployment option for
clients whose contracts require it.

## 9. Known limitations (stated in the product, not buried)

1. **Detection recall < 100 %.** Measured, published per type, per release.
2. **Quasi-identifiers are not solved.** "VP Engineering, Pune, L6, joined 2019"
   identifies one person with no name present. Phase 1 mitigation is policy
   (GENERALIZE grade/location for sensitive purposes) and data minimisation in
   the calling app. A k-anonymity-style risk score over ALLOWed attributes is a
   Phase 2 research item, not a promise.
3. **Streaming output NER is audit-only** (ADR-0008).
4. **The model may produce sensitive inferences** ("this pattern suggests a
   medical condition") from non-sensitive inputs. Out of scope for masking;
   addressed by purpose restriction and output policy.
5. **Pseudonymised ≠ anonymous.** Everything sent is still personal data for
   legal purposes; provider contracts and DPDP obligations still apply.
6. **Tasks that need the exact value cannot be served by masking.** They are
   routed to an in-boundary model or refused (DENY_ROUTE_LOCAL).

## 10. Decision index

| ADR | Decision |
|---|---|
| 0001 | Owned proxy + pure core library; modular monolith; only gateway holds provider keys |
| 0002 | Model sees scoped HMAC aliases; crypto only at rest |
| 0003 | Ephemeral per-request mapping; durable vault only where proven necessary |
| 0004 | Purpose declared by registered app, never inferred from the prompt |
| 0005 | Detection cascade + known-value propagation + detection cache |
| 0006 | In-process signed policy decision tables; OPA deferred |
| 0007 | Egress invariant, fail-closed matrix, PII-free telemetry |
| 0008 | Incremental streaming restore; output gate on aliased text |
| 0009 | Tool results and RAG are enforcement points |
| 0010 | Keyed-hash restore ledger catches restored values re-sent in truncated history |
