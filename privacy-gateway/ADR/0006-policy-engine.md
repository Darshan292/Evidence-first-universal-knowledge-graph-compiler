# ADR-0006: Policy engine — typed decision tables in-process; OPA deferred

- **Status:** Proposed (Phase 0)
- **Date:** 2026-09-30
- **Supersedes:** none

## Context

The report proposes OPA/Rego. Policy here is evaluated **per entity**, many
entities per request, on the hot path. OPA from Python means a sidecar HTTP
call (latency, another plaintext-adjacent process) or WASM with no maintained
Python binding we could verify. Rego is also a language the unit would have to
learn and review.

What OPA would actually give us — policy as reviewed code, testability,
decision logs — we can get without it.

## Decision

**Policies are versioned YAML packs compiled at load time into immutable
in-process decision tables. Evaluation is a pure function.**

```python
decide(entity_type, data_class, tenant, app, purpose, destination) -> Action
Action = TOKENIZE | REDACT | GENERALIZE(bucket_spec) | ALLOW | BLOCK_REQUEST | DENY_ROUTE_LOCAL
```

```yaml
pack: hris-default@3
applies_to: { purposes: [hr_case_summary, hr_email_draft] }
default: TOKENIZE                      # unknown/unlisted types are never ALLOW
rules:
  - { types: [AADHAAR, PAN, BANK_ACCOUNT, CARD, SECRET], action: BLOCK_REQUEST }
  - { types: [PERSON, EMAIL, PHONE, EMPLOYEE_ID, ADDRESS], action: TOKENIZE }
  - { types: [COMPENSATION], action: TOKENIZE }
  - { types: [COMPENSATION], purposes: [comp_band_analysis], action: GENERALIZE, bucket: inr_lakh_5 }
  - { types: [DATE_OF_BIRTH], action: GENERALIZE, bucket: age_decade }
  - { types: [ORG], data_class: [INTERNAL], action: ALLOW }
destination_overrides:
  local-vllm: { max_action_relaxation: ALLOW }   # in-boundary model may see more
restore:
  allowed: true
  never_restore: [SECRET]
```

Rules:

1. **Most-restrictive wins** when several rules match. Ordering is not semantics.
2. **Default is never ALLOW.** A new entity type starts tokenised.
3. Packs are **signed and versioned**; the gateway refuses unsigned packs in
   production. Pack id+version is in every decision record.
4. **Policy tests are mandatory**: each pack ships a table of
   `(input context) → expected action` cases, run in CI. A **policy simulator**
   CLI shows, for a sample prompt, what every entity would become — without
   calling a model.
5. BLOCK_REQUEST returns a structured `422` naming entity *types* and offsets
   (never values) so the app can tell the user what to remove.

## Consequences

**Good.** Microsecond evaluation, no extra process, reviewable diffs, testable.

**Bad.** We own a small DSL. It must stay small: if request-level
authorisation needs relationships (e.g. "manager may summarise only their
reports' cases"), that is an OPA/Cedar problem — revisit in Phase 5 with the
tool gateway, where it belongs.

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| OPA sidecar per entity | Latency × entities; another process seeing context. |
| Rego via WASM in Python | No verified maintained binding; opaque debugging. |
| Code-defined policy (Python ifs) | Not reviewable by privacy/legal; not diffable as policy. |
