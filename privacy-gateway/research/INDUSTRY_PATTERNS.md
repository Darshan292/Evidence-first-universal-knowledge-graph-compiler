# How industry does it: LLM privacy layers (research notes, 2026-09-30)

## 1. Sourcing caveat
**Update 2026-09-30 (second pass):** Cloudflare and SAP rows re-verified against primary doc sources on GitHub (cloudflare-docs, SAP-docs); Google Model Armor proto confirms StreamSanitizeUserPrompt/StreamSanitizeModelResponse RPCs but no tool-call field. No vendor publishes PII-masking latency. Full evidence: `VERIFICATION_2026-09-30.md`.

WebFetch was egress-blocked for the primary pages (help.salesforce.com, developers.cloudflare.com, docs.aws.amazon.com, help.sap.com). Everything below comes from WebSearch result summaries that quote or cite those pages, so it is secondhand. Re-verify specifics against the linked URLs. Anything not sourced is marked UNVERIFIED. Almost no vendor-documented latency numbers were found.

## 2. Comparison

| Vendor | Where masking sits | Reversible? | Streaming behaviour | Tool-call coverage | Documented limitation |
|---|---|---|---|---|---|
| Salesforce Einstein Trust Layer | Gateway between grounding and LLM | Yes (stored placeholder map) | UNVERIFIED | Masking disabled for agents | Limited entities/languages for pattern masking; no 100% accuracy; quality loss |
| Cloudflare AI Gateway DLP | Gateway, request and response scan | No: pass/flag/block only, no redaction (primary) | Full response buffered before inspection when response scanning is on; request-only DLP does not buffer (primary) | Tool args/results scanned only as text in the JSON body (primary) | TTFT grows with generation time |
| Cloudflare AI Gateway Guardrails (separate feature) | Gateway | n/a (content safety) | REST endpoints: streamed response logged, not enforced; gateway endpoints: full buffer, returned non-streamed (primary) | UNVERIFIED | Streaming enforcement gap |
| AWS Bedrock Guardrails | Model-boundary guardrail | Tags only (`[NAME-1]`), one-way | Sync buffers; async cannot mask | Not checked by default | Async can leak unfiltered chunks |
| Google Model Armor + SDP | De-identify template on prompt/response | Yes with key (AES-SIV/FPE) | UNVERIFIED | UNVERIFIED | Latency UNVERIFIED |
| Microsoft (Presidio, PII Shield) | Proxy before LLM call | Placeholders (PII Shield) | UNVERIFIED | UNVERIFIED | Purview DSPM for AI detects/classifies/blocks; no evidence it masks prompts. Content Safety PII masking UNVERIFIED |
| Skyflow | Vault, tokenize before LLM | Yes (policy-gated detokenize) | UNVERIFIED | UNVERIFIED | Vendor claims only |
| SAP AI Core orchestration | Optional module in orchestration workflow (SAP DPI) | Pseudonymization yes; anonymization no | Streaming unmasking works; a `MASKED_ENTITY_x` tag split across chunks is carried whole into the next chunk; SAP states small chunks reduce unmasking accuracy (primary) | Tool-call args unmasked outbound and re-masked inbound, pseudonymization only (primary) | 27 entity types, many locale-limited (person names English-only, addresses US-only); no latency published |
| Uber GenAI Gateway | Gateway redactor before third-party vendors | Yes (mapping used to un-redact) | UNVERIFIED | UNVERIFIED | Latency, quality loss, caching/RAG breakage |

## 3. Per-vendor notes

### Salesforce Einstein Trust Layer
- Order: secure retrieval and dynamic grounding, masking, prompt defense, LLM call, demasking, toxicity scoring, audit trail.
- Modes: pattern-based and field-based. Pattern detection mixes ML (person/company names), regex, and nearby context words.
- Documented pattern entities: company name (model), credit card, email, IBAN (regex + context), SSN (digit count/format). Languages: English, French, German, Italian, Japanese, Spanish. Field-based covers all Salesforce languages.
- Limits: discrete entity/language list; no model guarantees 100% accuracy; cross-region use hurts detection. Docs say masking is disabled for agents and can degrade answers.
- Masking "off by default" everywhere: UNVERIFIED.
- Zero retention is contractual with OpenAI/Azure OpenAI ([arXiv 2510.11558](https://arxiv.org/pdf/2510.11558)).
- Audit trail stores original prompt, masked prompt, response, feedback. Toxicity is per-category 0-1 scores. Grounding honours field-level security and RBAC.
- URLs: [trust arch](https://help.salesforce.com/s/articleView?id=ai.generative_ai_trust_arch.htm&language=en_US&type=5), [dev blog](https://developer.salesforce.com/blogs/2023/10/inside-the-einstein-trust-layer), [considerations](https://help.salesforce.com/s/articleView?id=sf.generative_ai_trust_data_mask_considerations.htm&language=en_US&type=5), [Agentforce limits](https://help.salesforce.com/s/articleView?id=ai.agent_trust_data_masking.htm&language=en_US&type=5).

### Cloudflare AI Gateway DLP
- Scans request and response bodies against DLP profiles. Actions: Flag, Block (Ignore in Guardrails). No reversible masking found.
- With response scanning on, the full provider response is buffered before return. Workaround: request-only scanning or a separate gateway.
- SSE and tool-call coverage: UNVERIFIED.
- URLs: [DLP](https://developers.cloudflare.com/cloudflare-one/data-loss-prevention/), [guardrails](https://developers.cloudflare.com/ai-gateway/features/guardrails/), [issue #28325](https://github.com/cloudflare/cloudflare-docs/issues/28325).

### AWS Bedrock Guardrails
- Actions: BLOCK or MASK (`[NAME-1]`, `[EMAIL-1]`). Built-in PII detection is ML/context based; custom regex supported. Masking is one-way.
- Streaming: sync (default) buffers and scans before release; async releases immediately, unfiltered content can reach users, masking unsupported.
- Tool parameters and tool interactions are outside the model boundary and unchecked by default; a Strands SDK workaround exists.
- Latency and pricing: UNVERIFIED.
- URLs: [sensitive filters](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html), [use cases](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use.html), [tokenization blog](https://aws.amazon.com/blogs/machine-learning/integrate-tokenization-with-amazon-bedrock-guardrails-for-secure-data-handling/), [tool blog](https://aws.amazon.com/blogs/security/extend-amazon-bedrock-guardrails-to-tool-interactions-using-the-strands-agents-sdk/).

### Google Model Armor + Sensitive Data Protection
- Advanced mode uses an inspect template and a de-identify template on prompts and responses.
- SDP transforms: deterministic AES-SIV and FPE (FFX). Keys are AES keys wrapped by Cloud KMS. Optional surrogate infotype annotation prefixes encrypted values so they can be re-identified.
- Reversible with the key. HMAC and "context tweak" specifics, response de-identification, streaming, latency: UNVERIFIED.
- URLs: [sanitize](https://docs.cloud.google.com/model-armor/sanitize-prompts-responses), [transformations](https://docs.cloud.google.com/sensitive-data-protection/docs/transformations-reference), [pseudonymization](https://docs.cloud.google.com/sensitive-data-protection/docs/pseudonymization).

### Microsoft
- Presidio: open-source analyzer (regex, NER, context; returns spans with scores) plus anonymizer.
- PII Shield: proxy that swaps PII for stable placeholders before the LLM call ([blog](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/introducing-pii-shield-a-privacy-proxy-for-every-llm-call/4514726)).
- Azure AI Content Safety, Purview DSPM for AI, Microsoft internal Presidio use: UNVERIFIED.

### Skyflow
- Vault with patented polymorphic encryption and schema-based tokenization (per-column token maps, transient tokens). Detokenization gated by fine-grained policy, including output format (e.g. partial redaction).
- "50+ types, multiple languages" is a vendor claim. Streaming and latency: UNVERIFIED.
- URL: [product](https://www.skyflow.com/product/pii-data-privacy-vault).

### SAP AI Core orchestration
- Optional data-masking module powered by SAP Data Privacy Integration. Does nothing if not configured.
- Anonymization: `MASKED_ENTITY`, irreversible. Pseudonymization: `MASKED_ENTITY_ID`, response auto-unmasked.
- Entity list, streaming, latency: UNVERIFIED. Workday and Joule: UNVERIFIED, not researched.
- URL: [orchestration](https://help.sap.com/docs/sap-ai-core/generative-ai/orchestration).

### Private AI, Protecto, Lakera, Nightfall
- Private AI: UNVERIFIED, no primary source retrieved.
- Protecto: detect, tokenize with semantically meaningful tokens, send to LLM, restore for authorised users ([blog](https://www.protecto.ai/blog/protect-pii-in-any-llm-platform/)).
- Lakera Guard: ML detectors via one API; can block or mask PII; returns match locations via payload parameter ([docs](https://docs.lakera.ai/docs/data-leakage-prevention)).
- Nightfall: external SaaS API, data transits third-party infrastructure before alert (secondary source, UNVERIFIED).

### Uber GenAI Gateway
- OpenAI-API-mirroring gateway. PII redactor runs before third-party vendors, using placeholders like `ANONYMIZED_NAME_`. Stored mapping is used to un-redact responses.
- Uber reports added latency, lost context hurting quality, and inconsistent placeholders breaking LLM caching and RAG. No numbers retrieved.
- URLs: [Uber blog](https://www.uber.com/us/en/blog/genai-gateway/), [InfoQ](https://www.infoq.com/news/2024/09/uber-genai-gateway-llm-openai/).
- Grab, LinkedIn, Airbnb, Intuit, Robinhood, Stripe: nothing found.
- Third-party latency ranges (UNVERIFIED as authoritative): regex sub-ms, NER tens of ms, external PII APIs ~100-200 ms ([TrueFoundry](https://www.truefoundry.com/blog/pii-redaction-llm-gateway-vs-application)).

## 4. Common patterns
- Masking sits in a proxy/gateway layer between app and LLM.
- Detection is hybrid: regex, checksums and context words for structured data; NER/ML for names and organisations.
- Reversibility is a mode choice: placeholder map (Salesforce, SAP pseudonymization, Uber, Protecto) or key-based deterministic encryption (Google); one-way for AWS tags and SAP anonymization.
- The placeholder-to-original mapping is held server-side for the request.
- Zero retention is mostly contractual, not architectural.
- Audit logging of original and masked prompts is first-class.
- Access control is enforced at retrieval time (field-level security, KMS, vault policy), separate from masking.
- Streaming forces a choice: buffer (safe, slower first token) or pass through (fast, leaky).

## 5. What none of them solve well
- Streaming with reversible masking: placeholders can split across chunks; AWS async cannot mask; Cloudflare buffers everything.
- Context loss versus privacy: Salesforce disables masking for agents; Uber reports quality loss.
- Detection recall across languages and regions: short entity/language lists, no accuracy guarantee.
- Tool calls and agent loops sit outside model-boundary filters (AWS); Cloudflare coverage unclear.
- Caching and RAG consistency: varying placeholders break caches and embeddings (Uber).
- Vendor-independent latency numbers are almost never published.

## 6. Implications for our gateway
- Streaming restore must be incremental with a bounded hold-back (Cloudflare buffers everything; AWS async cannot mask).
- Placeholders must be consistent across turns (Uber).
- Tool arguments and tool results are a separate enforcement point (AWS).
- Zero-retention is contractual, so minimise what leaves.
- Masking costs quality, so measure utility (Salesforce disables masking for agents).
