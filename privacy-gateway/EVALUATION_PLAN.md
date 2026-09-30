# Evaluation Plan — pre-registered

- **Status:** Proposed (Phase 0). Gates below are frozen before any system run;
  changing a gate after seeing results requires a dated amendment with reason.
- **Date:** 2026-09-30

A demo that masks "John Doe" is not evidence. This plan defines what counts.

## 0. Rules

1. **Held-out means held-out.** Gold data is split 60/20/20 (dev/tune/test) by
   *document template and name source*, not by row. Thresholds are tuned on
   `tune`; gates are read once on `test` per release.
2. **Gold is authored before the system runs on it**, by someone who did not
   write the detectors, and frozen (hash recorded).
3. **No LLM judge for detection metrics.** Span gold only. LLM judges are
   allowed for E-UTIL only, and must be calibrated against ≥ 100 human-rated
   items (report agreement).
4. **Report per entity type**, never only micro-averaged. A 0.97 micro-F1 that
   hides 0.60 recall on BANK_ACCOUNT is a failure.
5. **Synthetic-data bias is declared in every report.** Real HRIS data will not
   be available for evaluation; §E-DET-R describes the only acceptable proxy.

## E-DET — detection quality (gate for every release)

Corpus (synthetic, generated then hand-corrected):

| Slice | Size (min) | Content |
|---|---|---|
| HR free text | 1,500 docs | case notes, emails, appraisal comments, grievance summaries; Indian + global names incl. single-token names, initials, Hinglish |
| HRIS structured | 1,000 payloads | Workday/UKG-shaped JSON & XML (worker, comp, absence), CSV exports |
| Integration/debug | 500 items | logs, stack traces, auth headers, EIB/Studio-style XML, curl commands with tokens |
| Negatives | 1,000 items | text dense in look-alikes: invoice numbers, 12-digit order ids, public company names, dates, amounts that are not compensation |

Name sources must include ≥ 30 % names **absent** from any EDM dictionary, so
NER is actually tested.

**Statistical power (amendment 2026-09-30, before any run).** A recall gate
is meaningless if the test split holds 40 positives (one miss = 0.975). Minimum
*positive instances per type in the test split*: **≥ 150** for each HIGH type,
EMAIL, PHONE, EMPLOYEE_ID (zero misses at n = 150 bounds the true miss rate at
≈ 2 % with 95 % confidence, rule of three); **≥ 400** for PERSON; **≥ 200** for
COMPENSATION. Generators are sized to hit these after the 60/20/20 split. Every
per-type result is reported with its Wilson 95 % interval; a gate passes on the
point estimate **and** requires the interval's lower bound to be within 0.03 of
the gate. Types that cannot reach the minimum n are reported as *underpowered*,
not as passing.

Metrics: per-type precision, recall, F1 (exact span and overlap); **leak rate**
= fraction of gold sensitive characters that reach the egress body.

**Gates (test split):**

| Type group | Recall (overlap) | Precision |
|---|---|---|
| HIGH structured (Aadhaar, PAN, bank, card, secrets, passport…) | ≥ 0.98 | ≥ 0.90 |
| EMAIL, PHONE | ≥ 0.98 | ≥ 0.95 |
| PERSON in HR free text | ≥ 0.90 (P1 w/o NER: report only) → ≥ 0.93 (P2) | ≥ 0.85 |
| COMPENSATION | ≥ 0.95 | ≥ 0.85 |
| EMPLOYEE_ID (tenant formats) | ≥ 0.98 | ≥ 0.95 |
| Overall character leak rate on RESTRICTED slices | ≤ 1.0 % | — |

**E-DET-R (realism check):** with client/infosec approval only, run the
detector *inside* the client boundary on a sample of real records and report
**aggregate** per-type counts of detections vs. a human spot-check of 200
records. No raw data leaves; the output is numbers.

## E-ADV — adversarial robustness

Transformations applied to E-DET test items: homoglyphs, zero-width joiners,
spaced letters (`R a h u l`), dotted/obfuscated email (`rahul [at] acme [dot] in`),
digit grouping changes, OCR noise, mixed scripts (Devanagari names), line breaks
inside values, JSON-escaped strings, values split across messages.
Report recall delta vs. clean. **Gate:** HIGH types recall drop ≤ 3 pts.
Base64/hex-encoded payloads: measured and reported, **no gate in Phase 1**
(declared residual risk).

## E-FMT — alias round-trip fidelity (decides ADR-0002's open question)

Candidates: `[PERSON_7QX2MA]`, `<PERSON_1>`, realistic surrogate.
Tasks: summarise, draft email, extract to JSON, translate to Hindi, rewrite
formally. Models: every Phase 1 destination. n ≥ 500 per (format, model).
Metric: fraction of aliases restored exactly; fraction mangled-but-recoverable
(lenient matcher); fraction lost; fabricated-alias rate.
**Decision rule (pre-registered):** keep `[TYPE_xxxxxx]` unless another format
beats it by ≥ 3 pts exact-restore *and* ≥ 2 pts on E-UTIL.

## E-UTIL — task utility

Paired design: same task, same model, raw synthetic input vs. gateway-processed.
Tasks per purpose (HR case summary, email draft, ticket classification, JSON
extraction). Metrics: classification accuracy; extraction field accuracy;
summary quality via calibrated judge + 100 human ratings.
**Gate:** ≤ 5 % relative degradation per purpose, or the purpose's policy is
revisited (e.g. GENERALIZE instead of TOKENIZE).

## E-STREAM — streaming correctness

Golden streams replayed with chunk boundaries at **every offset** of every
alias, multi-choice streams, interleaved tool-call deltas, Anthropic
content-block streams, abrupt termination. **Gate:** 0 raw-alias emissions to
client, 0 un-restored complete aliases, 0 split-value leaks; TTFT overhead
p95 ≤ 50 ms.

### E-STREAM-T21 — re-ingested restored output (ADR-0010)

Multi-turn scripts (≥ 200) where the user turn that introduced a value is
dropped before turn *n*; NER is forced to miss (mock detector); the in-process
detection cache is cleared between turns; turns alternate between two gateway
pods. **Gate: 0 raw restored values in any egress body.** Also asserts
`400 PGW_CONVERSATION_ID_REQUIRED` for restore-enabled apps sending assistant
turns without a conversation id.

## E-EGRESS-FUZZ — bug-leak hunting

Property-based fuzzing (Hypothesis) over request shapes: nested tool results,
repeated values with different casing, overlapping entities, offsets after
normalisation, retries, provider errors mid-request. Oracle: the egress
invariant. **Gate:** 0 violations over 10⁶ generated requests per release.

## E-CANARY — telemetry leakage

Honeytoken PII through every endpoint and error path (including forced
exceptions, timeouts, 4xx/5xx from provider). Grep logs, traces, metrics,
ledger, crash output. **Gate:** 0 hits. Runs in CI and nightly in production.

## E-PERF — latency and throughput

Workloads: 1 KB / 8 KB / 32 KB prompts; 1 / 10 / 30-turn conversations;
structured payloads. Report p50/p95/p99 added latency per stage, TTFT delta,
RPS per pod, NER pool utilisation, cache hit rate.
**Gates:** DESIGN N-1, N-2.

## Reporting

Each release produces `EVAL_REPORT_<version>.md`: gold hash, detector-set
version, policy pack versions, per-type table, failures by category with 20
sampled misses each (synthetic data, so safe to show), and an explicit
"what these numbers do not show" section.
