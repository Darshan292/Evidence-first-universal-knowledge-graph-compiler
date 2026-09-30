# ADR-0004: Purpose is declared and bound to the caller, never inferred

- **Status:** Proposed (Phase 0)
- **Date:** 2026-09-30
- **Supersedes:** none

## Context

The report's best idea is task-awareness: "does the model need the raw value?"
Its policy examples key on `context: hr_case`, `context: salary_band_analysis`.
It never says where `context` comes from. The two options are:

- **Infer it** from the prompt with a classifier or an LLM. Unreliable, and
  attacker-controlled: the user writes the prompt, so the user picks the policy.
- **Declare it**: the calling application states its purpose; the gateway
  verifies the application is registered for that purpose.

## Decision

**Every request carries a purpose. The purpose must be one the calling
application is registered for. Policy is selected by (tenant, app, purpose,
destination) — never by prompt content.**

```yaml
# app registry (versioned, reviewed like code)
apps:
  - app_id: hr-case-assistant
    tenant: acme
    owner: esu-hris-team@...
    auth: mtls + api_key
    purposes: [hr_case_summary, hr_email_draft]
    data_classes_allowed: [INTERNAL, CONFIDENTIAL, RESTRICTED]
    destinations: [azure-openai-in-central, local-vllm]
    conversation_scope: allowed        # may send X-PGW-Conversation-Id
    restore_output: true               # may receive re-identified output
```

Request headers (OpenAI body stays untouched):

```
X-PGW-Purpose: hr_case_summary          # required
X-PGW-Conversation-Id: 7f3c…            # optional; enables conversation-scoped aliases
X-PGW-Data-Class: RESTRICTED            # optional hint; can only RAISE, never lower
```

Rules:

1. Missing or unregistered purpose → `403`, fail closed.
2. A purpose is a *contract*: it fixes the policy pack, allowed destinations,
   and whether output may be restored. This is DPDP purpose limitation
   expressed as configuration.
3. The "exact salary lookup" case from the report is **not an LLM purpose**.
   It is a direct authorised API call in the application. The gateway's job is
   to refuse to be the path for it (policy action `DENY_ROUTE_LOCAL`), not to
   perform it.

## Consequences

**Good.** Policy cannot be steered by prompt injection. Every decision is
attributable to an owned, reviewed registration. Auditors get a clean answer
to "who is allowed to send what to whom, for what".

**Bad.** Applications must be onboarded. That friction is intended.

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Intent classifier on the prompt | User-controlled input selecting its own security policy. |
| One global policy | Either too strict for drafting tasks or too loose for payroll data. |
