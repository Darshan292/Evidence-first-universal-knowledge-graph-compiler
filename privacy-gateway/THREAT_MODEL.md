# Threat Model

- **Status:** Proposed (Phase 0)
- **Date:** 2026-09-30
- **Scope:** ESU Privacy Gateway (DESIGN.md, ADR-0001...0009). Controls listed are designed, not built. No control below is claimed to eliminate its threat; "Residual" states what remains.

## 1. Assets

| Asset | Where | Why it matters |
|---|---|---|
| Raw sensitive values | Gateway process memory for request lifetime (ADR-0003) | Plaintext HR/ERP data; the gateway sees all of it (ADR-0001 accepted risk) |
| Provider credentials | Gateway only (ADR-0001) | Bypass, cost abuse, provider-side impersonation |
| Tenant keys (`k_tenant_alias/edm/cache/kek`, DESIGN 5.3) | Sealed secret (P1), KMS/HSM (P3) | Alias confirmation, dictionary inversion, vault decryption |
| Policy packs + `k_sign_policy` | Signed YAML (ADR-0006) | Whoever edits policy decides what leaves |
| EDM dictionary | HMAC'd values (ADR-0005 D3) | Reveals which names/IDs the tenant holds if key leaks |
| Egress ledger | PostgreSQL, no content (ADR-0007) | Metadata: who sent what type, when, to whom |
| Mapping store (P3) | Encrypted, TTL'd (ADR-0003) | Highest-value at-rest target; is personal data |
| Restore capability | `restore_output`, re-identification API | Turns aliases back into people |

## 2. Actors

| Actor | Capability assumed |
|---|---|
| A1 Curious/compromised LLM provider | Sees every outbound byte and logs it; may be breached later |
| A2 Malicious or careless internal user | Authenticated to an app; crafts prompts, pastes data |
| A3 Compromised calling app | Holds valid mTLS cert + app key; can set headers |
| A4 Insider with gateway ops access | kubectl/shell, config, possibly key material |
| A5 External network attacker | Position on network; no valid credentials |
| A6 Supply-chain attacker | Malicious PyPI package or model weights |
| A7 Prompt-injection content | Instructions inside documents, tool results, RAG chunks |

## 3. Trust boundaries (DESIGN 3)

| ID | Boundary | Crossing controls |
|---|---|---|
| B1 | App to gateway | mTLS + API key, registry, purpose check (ADR-0004) |
| B2 | Gateway to provider | Detect/transform, egress invariant, ledger (ADR-0007); egress network policy |
| B3 | Provider response to client | Output gate then restorer (ADR-0008) |
| B4 | Gateway to NER pool (P2) | Separate process/deployment; no external network (T11) |
| B5 | Gateway to ledger/mapping DB/KMS | Content-free ledger; envelope encryption (ADR-0003) |
| B6 | Tool/RAG to model context | Same pipeline as user input (ADR-0009) |
| B7 | Ops/CI to gateway | Signed images, signed packs, pinned deps (DESIGN 6) |

## 4. Threats

Test IDs are suites in EVALUATION_PLAN.md: E-DET, E-ADV, E-FMT, E-UTIL, E-STREAM, E-CANARY, E-EGRESS-FUZZ, E-PERF. "Review" = design/ops review, not automated.

| ID | Threat | STRIDE | Actor | Control | Residual risk | Verified by |
|---|---|---|---|---|---|---|
| T1 | Accidental PII egress (unmasked second occurrence, offset bug, retry re-sends original, unprocessed field) | I | A2, bug | D5 propagation (ADR-0005); egress invariant on exact bytes (ADR-0007); unknown fields in content positions rejected (DESIGN 4.3) | Invariant checks only known entities (T20); implementation bugs in sweep itself | E-EGRESS-FUZZ, E-CANARY |
| T2 | Detector miss (name, novel ID format, free-text health detail) | I | A2 | Cascade D0-D5, per-type thresholds tuned for recall on RESTRICTED (ADR-0005); HIGH types default BLOCK (DESIGN 5.2) | Recall < 100 % by design (DESIGN 9.1); NER weaker on Indian names/code-mixed text | E-DET |
| T3 | False positives block or degrade legitimate work | D | A2 | Per-type thresholds, allow-lists, fusion penalties (ADR-0005); INTERNAL tuned for precision | Users see blocks/utility loss; pressure to loosen policy | E-DET, E-UTIL |
| T4 | Alias linkability across scopes/tenants | I | A1 | Per-scope HKDF key, per-tenant root (ADR-0002); default random scope per request | Within a conversation scope aliases are stable by design; rotation window is a bounded linkage; alias reveals entity type | E-FMT, Review |
| T5 | Unauthorised restore / re-identification | E, I | A3, A4 | `restore_output` per app (ADR-0004); `never_restore: [SECRET]` (ADR-0006); P3 re-id is separate identity, purpose-bound, rate-limited, audited (ADR-0003) | Compromised app with `restore_output: true` legitimately receives restored text | E-STREAM, Review |
| T6 | Model output leaks or fabricates: invented aliases, raw PII the model "knows", EDM names | I, T | A1, A7 | Output gate on aliased text: fabrication check, D2 recognisers, EDM Aho-Corasick (ADR-0008) | Streaming output NER is audit-only, not prevention; non-EDM names in output can pass in streaming mode | E-STREAM, E-ADV |
| T7 | Tool-call args or tool results carry PII | I | A2, A7 | Tool results treated as input, D1 structure-aware detection (ADR-0009); args gated whole then restored only if app allows; P5 tool gateway allow-lists | Phase 1 has no tool allow-listing; restored args go to app-side tools, which are out of gateway control | E-DET, E-STREAM |
| T8 | RAG leakage: raw chunks retrieved, ACL bypass, embeddings of raw text | I | A2, A7 | Ingest via core, tokenised chunks, local embeddings, ACL filter by requesting user, re-check at assembly (ADR-0009). Phase 4, not in MVP | Ingest-time misses persist in corpus; ACL sync lag; not covered before Phase 4 | E-DET, E-EGRESS-FUZZ |
| T9 | Logging/telemetry/crash-report leakage | I | A4, bug | Allow-listed log schema, sanitised exceptions, no locals capture, no payload capture (ADR-0007) | Third-party libs may log; new code paths need coverage | E-CANARY |
| T10 | Evasion: Unicode, homoglyphs, zero-width, splitting ("R a h u l"), leetspeak, base64/encoded | I | A2 | D0 normaliser with offset map (ADR-0005). Spaced and homoglyph forms partly handled | **Base64/encoded payloads are NOT detected in Phase 1 (residual).** Leetspeak, cross-message splitting, languages with weak NER also residual | E-ADV |
| T11 | Supply chain: Presidio, GLiNER weights, LiteLLM-style PyPI compromise | T, E | A6 | `pip --require-hashes`, SBOM, signed images (DESIGN 6); model weight checksum verified at load; NER pool has no network egress (B4); no LiteLLM in plaintext path (ADR-0001) | Malicious code in a pinned-but-already-compromised release; poisoned weights that mis-detect selectively | Review, E-DET (regression on weight change) |
| T12 | Excessive agency: model-driven write actions | E | A7 | P5 tool policy, per-(app, purpose) allow-list, human approval for writes, user's own HRIS permissions (ADR-0009) | Not implemented until Phase 5; before that, apps own this risk | Review |
| T13 | Apps bypass gateway and call providers directly | S, E | A2, A3 | Only gateway holds provider keys; egress network policy (ADR-0001, DESIGN 3) | Exfil via non-provider channels, shadow API keys held by app teams, misconfigured policy | Review (network policy test) |
| T14 | Prompt injection: reconstruct identity from quasi-identifiers, or emit aliases in altered form (spaced, encoded, partial) to dodge restore/gate | I | A7 | Policy chosen by registered purpose, not prompt (ADR-0004); fabrication gate; lenient restore accepts only mangled forms in scope (ADR-0002/0008); GENERALIZE for sensitive purposes | Quasi-identifier re-identification unsolved (DESIGN 9.2); altered aliases may leak un-restored, which is safe but visible; model inferences (DESIGN 9.4) | E-ADV, E-STREAM |
| T15 | Purpose spoofing: app claims a purpose it is not registered for | S, E | A3 | Registry check on (app, purpose), 403 fail closed; `X-PGW-Data-Class` can only raise (ADR-0004) | Compromised app can use any purpose it *is* registered for; registry review is human | E-DET (policy tests), Review |
| T16 | Memory disclosure: core dumps, swap, heap in crash reports | I | A4, A5 | Core dumps off, no swap, read-only FS, no shell, zeroise map on close (ADR-0003, DESIGN 6) | Python cannot guarantee zeroisation; live memory readable by root on host; RAM-scraping | Review |
| T17 | `k_tenant_alias` compromise: attacker with key + alias tests low-entropy candidates (phones, 12-digit IDs) | I | A4, A1 | 30-bit truncated HMAC: aliases alone are not a dictionary oracle (ADR-0002); key hierarchy, KMS in P3, versioned rotation (DESIGN 5.3) | With key, alias gives candidate *confirmation* for enumerable domains; key in sealed secret in P1 is weaker than KMS | Review |
| T18 | DoS: huge payloads or adversarial text inflate NER cost | D | A2, A5 | Size limits, per-class detection deadline, fail closed (ADR-0005/0007); NER pool separate | Attacker can cause AI downtime for RESTRICTED by design; needs rate limits per app | E-PERF |
| T19 | Cross-tenant leakage via shared cache/process | I | A2, bug | Per-tenant cache HMAC key, no cross-tenant cache (DESIGN 8); values not stored in cache (ADR-0005) | P1 may share one process across tenants; memory not isolated; dedicated pods optional | E-CANARY, E-EGRESS-FUZZ |
| T20 | False sense of security: egress invariant only covers what WAS detected | I | all | Stated in DESIGN 9 and here; invariant is a bug detector, not a recall control; E-DET publishes recall | Users may treat "invariant passed" as "no PII sent" | E-DET, E-CANARY |
| T21 | Re-ingestion of restored output: client truncates history, re-sends an assistant message containing a value the gateway restored; only occurrence left is in text a detector may miss | I | bug, A2 | Restore ledger: keyed-hash record of every restored value per conversation, matched by D3b before other detectors; conversation id mandatory for multi-turn restore; fail closed if ledger unavailable (ADR-0010) | Values the *user* re-types in altered form still depend on normal detection; with `k_tenant_rl`, low-entropy values confirmable (as T17) | E-STREAM-T21, E-CANARY |

## 5. Residual risks we accept and must disclose

- Detection recall is below 100 % per type; published per release (DESIGN 9.1).
- Base64, other encodings, leetspeak and cross-message splitting are not detected in Phase 1.
- Quasi-identifiers (title + location + grade + date) can re-identify people with no name present.
- Pseudonymised data is still personal data under DPDP; provider contracts still apply.
- Streaming output NER is audit-only; prevention needs buffered or non-streaming mode (ADR-0008).
- Raw values sit in gateway memory for the request lifetime; a host-level attacker can read them.
- The gateway is a single high-value plaintext target; compromise exposes live traffic.
- Fail-closed means detector or NER outages become AI outages (DESIGN N-3).
- A compromised app with `restore_output: true` receives restored data by design.
- Model-generated sensitive inferences from non-sensitive input are not masked (DESIGN 9.4).
- Tool gateway (P5), RAG controls (P4) and durable-store controls (P3) do not exist in Phase 1; their rows are design intent.
- Insiders with key custody (A4) can defeat alias and EDM protections; mitigated by separation of duties and audit, not eliminated.
- Alias type prefix discloses entity type to the provider (ADR-0002).

## 6. Out of scope

- Provider-side misuse, retention or training beyond what the contract and zero-retention terms cover.
- Compromised endpoint devices, browsers or screen-capture on users' machines.
- Legal or regulatory compliance determination (DPDP, GDPR); this document informs it, does not decide it.
- Correctness of upstream HRIS/ERP access control and data minimisation in calling apps.
- Model quality, hallucination and bias unrelated to identity leakage.
- Physical security and cloud-provider host compromise below the container runtime.
