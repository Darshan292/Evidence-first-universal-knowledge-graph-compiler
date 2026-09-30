# ADR-0005: Detection cascade, known-value propagation, and bounded cost

- **Status:** Proposed (Phase 0)
- **Date:** 2026-09-30
- **Supersedes:** none

## Context

Three facts shape detection:

1. **No single detector is good enough.** Published F1 for the GLiNER PII
   family sits around 0.75–0.83 (knowledgator edge→large, vendor-reported,
   UNVERIFIED). Presidio warns it cannot guarantee coverage. Regex alone misses
   every free-form name.
2. **NER is expensive.** GLiNER on CPU: ~125 ms median per ~500 bytes
   (pii-mcp measurement). A 16 KB chat history is **seconds** per turn on CPU.
3. **Chat history is re-sent every turn.** Naively, detection cost is O(n²) in
   conversation length: turn 20 re-detects turns 1–19.

The report lists detectors. It does not say how to make them affordable, or
how to combine them.

## Decision

### 1. The cascade (cheap → expensive, each stage narrows the next)

| Stage | Detector | Cost | Role |
|---|---|---|---|
| D0 | **Normaliser**: NFKC, zero-width/bidi strip, homoglyph fold, whitespace collapse; keep an offset map back to original bytes | µs | Defeats trivial evasion (T10). All detectors run on the normalised view; replacements apply to original offsets. |
| D1 | **Structure parser**: JSON / XML / CSV / key:value detection inside message content and tool results | µs–ms | Field names become context (`"annual_ctc": 2010000` is COMPENSATION with no NER). Values under known-sensitive keys are classified directly. |
| D2 | **Deterministic recognisers**: regex + checksum (Aadhaar-Verhoeff, GSTIN mod-36, PAN, IFSC, Luhn cards, IBAN, email, phone, secrets/JWT/API keys, ESU employee-ID formats) | sub-ms | High precision. A checksum is a filter, not proof (a single check digit accepts ~10 % of random input): checksum-valid matches still take a context score. Low-precision patterns (UAN, 12-digit, voter ID) require a keyword within a window. |
| D3 | **Exact-data match (EDM)**: HMAC'd dictionary of tenant-known values (employee names, IDs, customer/vendor names, project codenames) via Aho-Corasick on HMAC'd normalised n-grams | ms | Catches known entities NER misses (unusual names, codenames). Dictionary stores HMACs, not plaintext. |
| D4 | **NER**: GLiNER (ONNX) via Presidio `GLiNERRecognizer`, + spaCy for tokenisation | 10s–100s ms | Free-text names, orgs, locations, addresses. **Runs only on free-text segments** — never on values D1 already typed. |
| D5 | **Known-value propagation** | ms | Any value detected *anywhere* in the request is masked *everywhere* in the request (all turns, tool results, system prompt), by exact + canonical match. Turns one detection into full coverage. |

### 2. Fusion

Per candidate span: `score = max(detector scores) + context boost (keywords,
field name, source data class) − penalties (allow-list, stop-words, known
public entities)`. Overlapping spans resolve by: checksum-valid > EDM > longer
span > higher score. Entities of the same canonical value merge (entity
resolution v1 = canonical equality; fuzzy "R. Sharma ↔ Rahul Sharma" linking is
Phase 2 and only ever *adds* masking, never removes it).

Thresholds are **per entity type and per data class**, not global. For
`RESTRICTED` sources the threshold is set for recall; for `INTERNAL` for
precision. Thresholds are outputs of E-DET, frozen per release.

### 3. Cost control

- **Detection cache**: key = `HMAC(k_tenant_cache, normalised_message_bytes ‖ detector_config_version)`,
  value = span list (offsets + types + scores, **no values**), TTL = conversation
  TTL. Turn *n* runs NER only on the new message(s). Makes conversation cost
  O(n). Phase 1: in-process LRU with sticky routing on conversation id;
  Phase 3: shared cache.
- **Budget**: each request gets a detection deadline by data class. Exceeding it
  is a *failure*, handled by ADR-0007 (fail closed for RESTRICTED), never a
  silent skip of NER.
- **Inference pool**: GLiNER in a worker process pool (ONNX Runtime, int8
  quantised if E-DET shows ≤1 pt recall loss). GPU pool when p95 requires it.

### 4. Pluggability

```python
class Detector(Protocol):
    name: str; version: str
    def detect(self, doc: NormalisedDoc, ctx: DetectContext) -> list[Candidate]: ...
```

Every detector is versioned; the detector-set version is part of every decision
record and of the cache key.

## Consequences

**Good.** Recall comes from *combination* (structure + checksum + EDM +
NER + propagation), not from hoping one model is good. Latency is bounded and
linear in conversation length.

**Bad.** Four detector families to maintain and benchmark. The EDM dictionary
needs a sync job from the HRIS (Phase 4) — until then it is a static upload.

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Presidio alone | One detector's recall ceiling; no cost control; no structure awareness. It is *one stage*, not the design. |
| LLM-as-detector (remote) | Sends raw PII to a model to find PII. Defeats the purpose. |
| LLM-as-detector (local, 7B+) | 1–10 s per request on GPU; non-deterministic; reserved as an offline *auditor* for E-DET error discovery, not the hot path. |
| Global confidence threshold | Types differ by an order of magnitude in base precision. |
