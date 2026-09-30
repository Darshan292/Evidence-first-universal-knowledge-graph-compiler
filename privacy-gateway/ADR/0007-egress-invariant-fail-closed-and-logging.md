# ADR-0007: The egress invariant, fail-closed semantics, and PII-free telemetry

- **Status:** Proposed (Phase 0)
- **Date:** 2026-09-30
- **Supersedes:** none

## Context

Detection will miss things (ADR-0005). That is a statistical failure, measured
in E-DET. But there is a second, *non-statistical* class of failure that
destroys trust faster: bugs. An offset error after normalisation, an unmasked
second occurrence, a retry path that re-sends the original body, a tool-result
message that skipped the pipeline, an exception handler that logs the payload.
LiteLLM's guardrail failing **open** on analyzer error (#30728) is exactly this
class. Bugs are not solved by better models; they are solved by invariants.

## Decision

### 1. The egress invariant (checked on every upstream call)

> **No value that the pipeline itself classified as sensitive-and-not-ALLOW may
> appear in the bytes sent to the provider.**

Implementation: after serialising the final provider request body, run an
Aho-Corasick sweep over it for every original value (and its canonical forms)
in the request's entity set. **Any hit aborts the call** (`500 PGW_EGRESS_VIOLATION`),
emits a high-severity event, and increments a counter that pages someone.
Cost: sub-millisecond. This converts an entire family of implementation bugs
from "silent leak" into "loud outage".

The sweep runs on the **exact bytes** handed to the HTTP client — after all
adapters, retries and provider-specific rewrites — not on the IR.

### 2. The egress ledger

For every upstream call, persist: `request_id, tenant, app, purpose, provider,
model, sha256(body), entity_type_counts, policy_pack@version,
detector_set@version, decision (SENT|BLOCKED|VIOLATION)`. **No content.**
This is the audit answer to "prove what left, and under which rules".

### 3. Fail-closed matrix

| Condition | RESTRICTED / CONFIDENTIAL data class | INTERNAL |
|---|---|---|
| Detector error / timeout | **Block** (503, retryable) | Block |
| NER pool unavailable | **Block** | Degrade to D0–D3+D5 only, flagged in ledger (policy opt-in per app) |
| Policy pack missing/unsigned | Block | Block |
| Egress invariant hit | Block + page | Block + page |
| Unknown request shape / unsupported API | Reject 400 | Reject 400 |
| Mapping lost mid-stream | Terminate stream with error event; never emit raw aliases as if final | same |
| Output gate raises | Withhold response | Withhold response |

There is **no fail-open mode** for RESTRICTED. A config flag that would enable
one does not exist.

### 4. PII-free telemetry

- Structured logs only, from an **allow-listed field schema**. Free-text log
  messages cannot interpolate request data (lint rule + test that feeds
  canary PII through every code path and greps all sinks).
- Exceptions are caught at the boundary and re-raised with sanitised messages;
  stack-trace locals capture is disabled.
- OpenTelemetry spans carry counts, types, timings, versions — never content.
- Payload capture for debugging is **not a feature**. Debugging uses the
  policy simulator on synthetic data.
- **Canary test** (CI + nightly in prod): synthetic honeytoken PII sent through
  the gateway; any appearance in logs, traces, metrics, the ledger or provider
  mock is a failed build / P1 incident.

## Consequences

**Good.** The most likely real-world leak (a bug) becomes detectable by
construction. Audit evidence exists without storing content.

**Bad.** Some legitimate requests are blocked during detector outages.
That is the correct trade for HR data and must be agreed with app owners in
the onboarding contract.
