# Engineering facts: Indian identifiers, streaming, crypto, DPDP (2026-09-30)

Markers: [V] found in a source during research; [K] from prior knowledge, not re-verified; [?] unverified/uncertain.

## 1. India identifier recognizer spec

| ID | Format | Checksum | Regex | Precision | Keyword-gated? |
|---|---|---|---|---|---|
| Aadhaar [K] | 12 digits, first 2-9, 4-4-4 groups | Verhoeff | `(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)` | MEDIUM alone → HIGH with context | Checksum is a filter, not proof (see note) |
| Aadhaar VID [K][?] | 16 digits; checksum/first-digit rule unverified | unverified | `(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)` | MEDIUM | Prefer yes |
| PAN [K] | `AAAAA9999A`; 4th char = holder type (P person, C company, H HUF, F firm, A AOP, T trust, B BOI, L local authority, J artificial juridical, G govt); 5th = surname/entity initial | check letter algorithm not public | `\b[A-Z]{3}[ABCFGHJLPT][A-Z]\d{4}[A-Z]\b` | HIGH | No |
| GSTIN [K] | 15 chars: state 01-38 + PAN + entity `[1-9A-Z]` + `Z` + check | mod-36 | `\b(?:0[1-9]\|[12]\d\|3[0-8])[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b` | HIGH with checksum | No |
| IFSC [K] | 4 letters + `0` + 6 alnum | none | `\b[A-Z]{4}0[A-Z0-9]{6}\b` | HIGH | No |
| UAN [K] | 12 digits | none known | `(?<!\d)\d{12}(?!\d)` | LOW | YES |
| ESIC IP no. [K][?] | 17 digits, unverified | none known | `(?<!\d)\d{17}(?!\d)` | LOW | YES |
| Voter ID (EPIC) [K] | 3 letters + 7 digits | none | `\b[A-Z]{3}\d{7}\b` | LOW | YES |
| Bare 12-digit [K] | any 12 digits (Aadhaar/UAN ambiguity) | Verhoeff would resolve Aadhaar | `(?<!\d)\d{12}(?!\d)` | LOW | YES |
| Driving licence [K] | state(2) RTO(2) year(2-4) serial(7), 15-16 chars | none | `\b[A-Z]{2}[ -]?\d{2}[ -]?(?:19\|20)?\d{2}[ -]?\d{7}\b` | LOW-MEDIUM | YES |
| Passport [K] | letter (not Q/X/Z) + 7 digits; variants exist | none | `\b[A-PR-WY][1-9]\d{6}\b` | MEDIUM | Prefer yes |
| UPI ID [K] | `handle@psp` | none | `\b[\w.\-]{2,256}@[a-zA-Z]{2,64}\b` (match known PSP handles first: ybl, oksbi, okhdfcbank, paytm, upi, ibl, axl, apl; overlaps email) | MEDIUM | No |
| Mobile [K] | optional +91/91/0, then 10 digits starting 6-9 | none | `(?<!\d)(?:\+?91[\-\s]?\|0)?[6-9]\d{4}[\-\s]?\d{5}(?!\d)` | MEDIUM | No |

Verhoeff [K] (d = 10x10 dihedral multiplication table, p = 8-row permutation table, standard tables):
```
c = 0
for i, digit in enumerate(reversed(digits)):
    c = d[c][p[i % 8][digit]]
valid = (c == 0)
```

**Note (design review):** a single check digit accepts ~10 % of random inputs. In ERP text dense with 12-digit order/invoice numbers, Verhoeff alone yields a false-positive stream. Treat checksum-valid Aadhaar as MEDIUM and require a context signal (keyword, field name, 4-4-4 grouping) for HIGH; the same logic applies to GSTIN mod-36 (~1/36 pass rate, far better).

GSTIN mod-36 [K]:
```
charset = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
s = 0
for i, ch in enumerate(gstin[:14]):
    f = 1 if i % 2 == 0 else 2
    prod = charset.index(ch) * f
    s += prod // 36 + prod % 36
check = charset[(36 - s % 36) % 36]   # must equal gstin[14]
```

## 2. Compensation patterns [K]

- Indian grouping: 3 digits, then groups of 2 (`18,40,000`, `1,25,00,000`). Regex: `\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d+)?`. Accept Western grouping too.
- Prefix/suffix: `(?:₹|Rs\.?|INR)\s?` before; `/-` or "only" after.
- Units: `(?i)\b\d+(?:\.\d+)?\s?(?:L|Lac|Lacs|Lakh|Lakhs|LPA|Cr|Crore|Crores|k|CTC)\b` (e.g. `₹20.1 L`, `38 LPA`, `1.2 Cr`, `45K/month`).
- Context words that raise confidence: CTC, salary, fixed, variable, bonus, stipend, package, "hike of N%", band, grade. Bare numbers in columns named salary/CTC count as compensation.

## 3. Streaming de-tokenisation

Evidence [V]:
- LiteLLM issue #41611: a value split across two SSE chunks passes guardrails unmasked. https://github.com/BerriAI/litellm/issues/41611
- LiteLLM PR #41936 buffers the entire stream, scans, then emits one masked chunk (safe, but no first token until the model finishes). https://github.com/BerriAI/litellm/pull/41936 ; docs PR https://github.com/BerriAI/litellm-docs/pull/1790
- A related report: tokenizers routinely split a 12-char placeholder across 2-3 `text_delta` events, so single-frame replacement never matches.
- Presidio `output_parse_pii` keeps a per-request `pii_tokens` map (counter restarts per request) and does a literal replace.
- Portkey and SAP AI Core streaming masking behaviour: not found [?].

Hold-back algorithm [K] (longest-prefix match):
1. Append each delta to a buffer.
2. Replace all complete known placeholders (e.g. `<?[A-Z_]+_\d{3}>?`).
3. Find the longest buffer suffix that is a proper prefix of any active placeholder (or an open bracket such as `<`, `<PERS`, `[PER`). Hold it back.
4. Emit the rest; flush all at end of stream.
5. Cap hold-back at max placeholder length; add a timeout so the stream cannot stall.
6. Rewrite `content` per choice keeping the SSE envelope. Handle OpenAI `delta.content`, Anthropic `content_block_delta`, and `tool_calls` argument deltas separately.
Fixed-width, delimiter-anchored placeholders keep the hold-back short.

## 4. Placeholder and utility literature

- Balancing Privacy and Utility in Personal LLM Writing Tasks (masking, masking+context, pseudonymization): minimal response-quality loss, 97-99% entity masking [V, from search summary]. https://aclanthology.org/2025.privatenlp-main.3.pdf
- Tau-Eval: utility loss is task-dependent; sentiment robust, ANLI/MedNLI/fake-news degrade significantly [V, from search summary]. https://arxiv.org/pdf/2506.05979
- CAMP: consistent synthetic identities plus de-masking for multi-turn agents [V]. https://arxiv.org/html/2604.16521v1
- RAT-Bench https://arxiv.org/html/2602.12806v1 and survey https://arxiv.org/pdf/2508.21587: relevant, numbers not extracted.
- Note: no paper found measures verbatim fidelity by placeholder format. We must measure it ourselves (round-trip test on our target models, n>=500 per format).
- Folklore [K][?]: `<PERSON_1>` and `[PERSON_1]` usually copied verbatim (failure modes: dropped or re-cased); bare `PERSON_001` more likely reformatted; realistic surrogates are preserved but inflected/possessive-suffixed and can collide with real names.
- Trade-off [K]: tags give easy exact match and simple streaming but lose gender/culture cues; surrogates give better fluency but hard round-trip and collision risk. Default to tags; consider surrogates for names/numbers where reasoning needs plausibility. Format-preserving fake IDs must never be valid real numbers.

## 5. Latency [?]

- GLiNER [V]: 125 ms median / 175 ms p95 per ~500 B on an M-series CPU (https://github.com/foro-sh/pii-mcp/issues/41). GLiNER medium-v2.1 107 ms p50 on an L4 GPU (via https://superlinked.com/glossary/what-is-gliner, not re-checked).
- WARNING: at ~125 ms per 500 B, a 50 KB conversation history is ~100 chunks, i.e. roughly 12 s serial CPU cost. Long histories therefore cost multiple seconds unless cached per turn, batched, or run on GPU.
- Presidio spaCy `en_core_web_lg`: roughly tens of ms per 1 KB on modern CPU [K][?]; transformer recognizers hundreds of ms to seconds [K][?]. No published benchmark found; measure.
- OPA: sub-ms to a few ms for simple policies [K][?]; benchmark our policies.

## 6. Crypto

- AES-SIV [K]: `from cryptography.hazmat.primitives.ciphers.aead import AESSIV`; `key = AESSIV.generate_key(bit_length=512)` (valid 256/384/512 bits; two AES keys inside); `AESSIV(key).encrypt(data, associated_data=[b"tenant", b"PAN"])` / `.decrypt(ct, associated_data)`. Deterministic, no nonce, ciphertext = plaintext + 16-byte tag.
- HMAC tokenisation [K]: `token = base32(HMAC_SHA256(key_tenant_type, normalized_value))[:N]`. One-way, so keep a vault for reversal. Per-tenant/per-type keys or domain separation; N>=12; normalize first. Low-entropy values (10-digit mobiles) are brute-forceable if the key leaks.
- FF1 / FF3 [V]: the Feb 2025 second draft of SP 800-38G Rev.1 removes FF3 and FF3-1 and keeps only FF1; original SP 800-38G shown as withdrawn; Rev.1 is still a draft. https://csrc.nist.gov/pubs/sp/800/38/g/final ; https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-38Gr1-draft.pdf . Use FF1 only. Minimum domain size applies (10-12 digit domains near the floor) [K]. `cryptography` ships no FPE; need a third-party FF1 library [?].

## 7. DPDP Act 2023 / Rules 2025

- Timeline [V]: Rules notified 13 Nov 2025; 18-month phase-in for most obligations (to ~May 2027). https://www.seclore.com/fundamentals/dpdp-rules-2025-compliance-guide/ ; https://www.cy5.io/blog/dpdp-rules-2025-complete-compliance-guide-cloud-security/
- Breach, Rule 7 [V]: notify affected principals without delay; inform the Board without delay with initial details, then detailed report within 72 hours (extendable on request). https://www.dpdpa.com/dpdparules/rule7.html ; https://www.medianama.com/2025/11/223-data-breach-reporting-timeline-of-dpdp-rules-2025-explained/ . Penalty figures (one source said 200 crore; I believe safeguards-failure is up to 250 crore) need checking [?].
- Processors [K]: S.8(2) processor only under a valid contract; fiduciary remains responsible; S.8(5) reasonable security safeguards. Gateway and LLM vendor are both processors.
- Cross-border [V/K]: S.16 permits transfer except to Government-restricted countries; Rule 15 sets conditions; reported not yet operational. https://www.barandbench.com/columns/the-dpdp-cross-border-transfer-rules-arent-live-yet-so-why-are-contracts-being-redrafted-as-if-they-are . Stricter sector laws (e.g. RBI localisation) still apply; S.16(2) preserves them [K].
- Pseudonymised data [K][?]: the Act has no pseudonymisation carve-out; re-linkable data (vault/key) is personal data for the fiduciary. Whether tokens are personal data in the LLM vendor's hands is arguable; plan to treat them as personal data.

Engineering notes, not legal advice.
