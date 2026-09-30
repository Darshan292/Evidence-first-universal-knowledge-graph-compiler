# ADR-0001: System boundary and deployment shape

- **Status:** Proposed (Phase 0)
- **Date:** 2026-09-30
- **Supersedes:** none

## Context

The gateway sits between ESU applications (HRIS/ERP assistants, integration
tooling, RAG apps, agents) and LLM providers. It must see every byte that crosses
the trust boundary, in both directions, including streamed responses and tool
calls. Three shapes were available:

1. **A library** each application imports (`sanitize(prompt)` → `restore(resp)`).
2. **A plugin inside an existing LLM proxy** (LiteLLM / Portkey guardrail hook).
3. **An owned reverse proxy** that speaks the provider wire protocols, with the
   privacy engine as an internal module.

## Decision

**An owned, OpenAI-compatible reverse proxy (the *gateway*), containing a
transport-independent privacy core (`pgw-core`) as a library. The proxy is the
only process in the network that holds provider credentials.**

```
                ┌──────────────────────── ESU TRUST BOUNDARY ────────────────────────┐
 App / Agent ──►│ Gateway (async I/O)  ──►  pgw-core  ──► Provider adapter ──► egress │──► LLM provider
   (mTLS +      │   authn, app registry     detect → decide → transform → verify      │
   app key)     │   purpose binding         ◄── output gate ◄── de-tokenise ◄──────── │◄── stream
                └────────────────────────────────────────────────────────────────────┘
```

Binding rules:

1. **Provider credentials live only in the gateway.** Applications authenticate
   to the gateway, never to a provider. Network egress to provider hostnames is
   allowed only from the gateway's pods (egress policy). This, not the NER model,
   is what turns "please use the gateway" into "you cannot not use the gateway".
2. **`pgw-core` has no network or framework dependency.** It takes a normalised
   message IR and returns a transformed IR plus a decision record. The proxy,
   the batch RAG ingestor and the tool gateway all call the same core. One
   detector/policy implementation, three enforcement points.
3. **Detection inference is a separable pool.** The gateway is I/O-bound; NER
   inference is CPU/GPU-bound. In Phase 1 they run in one deployable with the
   model in a worker process pool behind an interface; the interface is the
   seam along which the inference pool is split out when load requires it.
4. **Modular monolith, not microservices.** One team, one deployable, clear
   module boundaries. Splitting services before measuring latency and load is
   cost without evidence.

## Consequences

**Good.** Enforcement cannot be bypassed by an application forgetting to call a
library. Streaming, tool calls and multi-turn state are visible in structured
form, which a generic proxy plugin hook does not guarantee. The core is testable
without HTTP.

**Bad.** We own wire-protocol compatibility (OpenAI Chat Completions, Responses,
Anthropic Messages). Mitigated by supporting a **small, explicit** protocol set
and rejecting everything else with a clear error rather than passing it through.

**Accepted risk.** The gateway is a high-value target: it sees all raw data in
plaintext in memory. It gets the hardening of a secrets service (see
THREAT_MODEL.md), not of a web app.

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Library in each app | Bypassable by construction; every app team re-implements streaming restore; no central audit. |
| Plugin inside LiteLLM/Portkey | Fastest demo. But the proxy framework then owns streaming chunking, retries, logging and error handling — exactly where PII leaks happen (logs, retries, partial chunks). A large third-party dependency in the plaintext path is also a supply-chain risk. We may reuse such a proxy *behind* our gateway as a provider router, never in front of it. |
| Service mesh / Envoy ext_proc filter | Right shape at large scale; wrong first step. Keep as the Phase 5+ target for the data plane once the core is proven. |
| Microservices from day one | No load evidence to justify it; multiplies the plaintext surface (more hops carrying raw data). |
