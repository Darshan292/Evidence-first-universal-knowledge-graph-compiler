# EVALUATION CORPUS

**Date:** 2026-09-22. Frozen before measurement (commit `d99756e`).

Three corpora, each with a distinct job. All are small enough to label
exhaustively — which is the point. A corpus you cannot label completely cannot
serve as gold.

---

## 1. `eval/corpus/` — the X-1 retrieval corpus

15 files, 6,596 bytes: a small but realistic service with code, documentation
and structured data that genuinely disagree with each other.

### Code — `orderflow/` (7 modules)

| Module | Role | Why it is here |
|---|---|---|
| `config.py` | `PAYMENT_TIMEOUT_SECONDS = 30`, `RETRY_LIMIT`, `SETTLEMENT_BATCH_SIZE` | the exact-identifier and multi-hop root |
| `gateway.py` | `CardGateway`, `GatewayError` | imports the timeout; the only network boundary |
| `payments.py` | `PaymentProcessor`, `build_processor` | orchestration; the multi-hop middle |
| `ledger.py` | `Ledger` | append-only record; imports batch size |
| `reporting.py` | **`Processor`** | **deliberate name collision** with `PaymentProcessor` |
| `api.py` | `post_charge`, `get_report` | entry points; the multi-hop head |
| `__init__.py` | package marker | — |

Dependency chain (the multi-hop target):
`api.post_charge → payments.build_processor → PaymentProcessor → CardGateway → config.PAYMENT_TIMEOUT_SECONDS`

### Documentation — `docs/` (5 files)

| File | Says | Why it is here |
|---|---|---|
| `architecture.md` | deadline is 30 s | agrees with code |
| `adr-007-payment-timeout.md` | 30 s, p99 was 18 s | rationale; the cross-document target |
| **`runbook.md`** | **deadline is 60 s**, "last reviewed 2024" | **the deliberate conflict + stale doc** |
| `checkout-abandonment.md` | shoppers give up after "about half a minute" | paraphrase target: describes the concept without the source terminology |
| `api.md` | POST /charge → `post_charge` | code↔doc linking |

### Structured data — `data/` (3 files)

`settings.json` (timeout 30, agrees with code) · `fees.csv` (4 rows) ·
`routing.xml` (a **45 s** APAC deadline — a regional override, not a conflict, to
test whether a retriever can tell the two apart).

### Deliberate properties

| Property | Where |
|---|---|
| Conflict | `runbook.md` 60 s vs everything else 30 s |
| Stale marker | `runbook.md` self-describes as last reviewed 2024 |
| Name collision | `reporting.Processor` vs `payments.PaymentProcessor` |
| Misleading lexical match | "processor deadline" matches `reporting.py` but the deadline is in `gateway.py` |
| Structural-not-lexical | "abandonment threshold" appears in **none** of the files it affects |
| Paraphrase target | `checkout-abandonment.md` |
| Unanswerable topics | nothing about Kafka, brokers, GDPR or retention |

## 2. `eval/rescorpus/` — the C-2 resolution corpus

11 Python files, one file per resolution case, so a failure localises to a case
rather than to "cross-module resolution".

| Case | File |
|---|---|
| direct import + qualified call | `direct_import.py` |
| aliased import (`as c`) | `aliased_import.py` |
| `from x import y as z` | `aliased_import.py` |
| plain `from x import y` | `from_import.py` |
| intra-module local function | `from_import.py` |
| same-name symbol in two modules | `pkg/core.py` + `pkg/other.py` |
| re-export through `__init__` | `pkg/__init__.py` + `reexport_import.py` |
| **imported name shadowed by a local def** | `shadowed.py` |
| third-party package absent | `third_party.py` |
| circular import, both sides | `cycle_a.py` + `cycle_b.py` |
| stdlib call | `pkg/core.py` |

## 3. `eval/malformed/` and `eval/unsupported/`

Five malformed Python files, each with a deliberate error at a known line and an
independently enumerated list of symbols a *recovering* parser should still find
(9 total). Plus `probe.js` and `Main.java` for the unsupported-language path.

---

## Gold sets

| File | Contents |
|---|---|
| `eval/EVALUATION_GOLD.json` | 23 queries across 12 classes; 21 answerable, 2 not |
| `eval/CODE_ANALYSIS_GOLD.json` | 13 call sites, 14 import edges |
| `eval/malformed/gold.json` | 5 cases, 9 recoverable symbols |

**Authoring order, which is the integrity guarantee:** all three were written by
reading the corpora directly and were **committed before any retrieval code
existed** (`d99756e`). No retrieval output influenced any entry.

`experiments/validate_gold.py` checks that every gold answer unit exists and that
every `evidence_must_contain` string is genuinely present in the cited source.
It passes.

### One gold correction, recorded

`eval/malformed/gold.json` v1.0.0 → v1.0.1: `m2_unclosed_paren.py` `error_line`
3 → 2. The unclosed bracket opens on line 2, so that is where parsing fails; line
3 is merely the first line consumed by the unterminated expression. An authoring
error, corrected **before** any parser comparison was scored, and it does not
touch `recoverable_symbols`, which is what scoring uses.

## Known limitations of this corpus

Stated plainly, because they bound every conclusion drawn from it:

1. **Too small for Recall@10 to discriminate.** 87 chunks; most classes saturate
   at 1.0 by R@10, so R@1 and MRR carry the signal.
2. **Only one of three paraphrase queries has genuinely zero vocabulary overlap**
   with its answer. The paraphrase class is therefore a weak test — see
   RETRIEVAL_EVALUATION §4.
3. **Synthetic and self-authored.** I wrote both the corpus and the gold, so the
   corpus cannot surface phrasing I did not think to include.
4. **No multi-repository, no large files, no real prose at scale.**
5. **Two negative queries** is a thin basis for a false-support rate.
