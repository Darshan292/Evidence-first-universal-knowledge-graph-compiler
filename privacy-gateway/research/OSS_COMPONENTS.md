# Open-source components: verified status (2026-09-30)

Sources: GitHub pages, PyPI JSON, search snippets. Egress-blocked: GitHub API, huggingface.co, arxiv.org, docs.litellm.ai, cryptography.io, openpolicyagent.org. Anything depending on those is UNVERIFIED. Stars are rough. Release dates from page summaries were sometimes inconsistent.

| # | Item | Exists / URL | License | Latest release (date) | Stars | Status |
|---|---|---|---|---|---|---|
| 1 | Presidio | https://github.com/data-privacy-stack/presidio (moved) | MIT | presidio-analyzer 2.2.364, 22 Jul 2026 (PyPI) | ~11.1k | Active |
| 2 | presidio-research | https://github.com/data-privacy-stack/presidio-research | MIT | UNVERIFIED | ~313 | Active |
| 3 | protectai/llm-guard | https://github.com/protectai/llm-guard | MIT | PyPI 0.3.16, 19 May 2025 | ~3.2k | ARCHIVED 9 Jul 2026 (confirmed) |
| 4 | GLiNER PII models | see notes | mixed | see notes | GLiNER ~4k | Active |
| 5 | Portkey-AI/gateway | https://github.com/Portkey-AI/gateway | MIT | v1.15.2 (year suspect, UNVERIFIED); 2.0 pre-release on 2.0.0 branch | ~13.1k | Active |
| 6 | LiteLLM | https://github.com/BerriAI/litellm | MIT; `enterprise/` separate license | PyPI 1.103.1 (date UNVERIFIED) | ~59.9k | Active |
| 7a | Envoy AI Gateway ("Agent Router") | https://github.com/envoyproxy/ai-gateway | Apache-2.0 | UNVERIFIED | ~2.2k | Active |
| 7b | Kong AI PII Sanitizer | https://developer.konghq.com/plugins/ai-sanitizer/ | Enterprise-only | n/a | n/a | Active |
| 8 | NeMo Guardrails | https://github.com/NVIDIA-NeMo/Guardrails | Apache-2.0 | 0.24.1 (date UNVERIFIED) | ~7.2k | Active |
| 9 | OPA | https://github.com/open-policy-agent/opa | Apache-2.0 | v1.21.1, 29 Sep 2026 | ~12.3k | Active (CNCF graduated) |
| 10 | cryptography | https://github.com/pyca/cryptography | Apache-2.0 OR BSD-3-Clause | 50.0.1 (PyPI; date UNVERIFIED) | ~7.8k | Active |
| 11 | carnaval | https://github.com/carnaval-ai/carnaval | Apache-2.0 | v0.2.3 (date UNVERIFIED) | 1 | Beta, 42 commits |
| 12a | truefoundry/custom-guardrails-template | https://github.com/truefoundry/custom-guardrails-template | UNVERIFIED | none | 2 | Not archived |
| 12b | guardrails-ai/guardrails_pii | https://github.com/guardrails-ai/guardrails_pii | Apache-2.0 | UNVERIFIED | ~23 | ARCHIVED |

## Notes

**1. Presidio**
- Move to `data-privacy-stack/presidio` confirmed (repo banner; PyPI homepage). Stays MIT. Docker images moved from `mcr.microsoft.com/presidio-*` to `ghcr.io/data-privacy-stack/presidio-*`; legacy MCR images are no longer updated. Source: https://github.com/data-privacy-stack/presidio/blob/main/docs/project_transition.md
- PyPI extras: gliner, transformers, stanza, langextract, azure-ai-language, server, ahds. GLiNER and transformers recognizers supported; ONNX backend for GLiNERRecognizer in 2.2.362. Python 3.10-3.14. https://pypi.org/project/presidio-analyzer/
- Batch: REST batch processing and batch deanonymization added in 2.2.361. True async: UNVERIFIED.
- Releases page showed 2.2.364 with a 2024 date; PyPI says 22 Jul 2026. PyPI trusted.

**2. presidio-research.** Under new org; no evidence microsoft/presidio-research redirects. Evaluation data generation and notebooks.

**3. llm-guard.** Banner: "THIS PROJECT HAS BEEN ARCHIVED... no longer under active development or maintained"; read-only; HF models unmaintained.

**4. GLiNER PII models** (HF blocked; from search snippets)
- urchade/gliner_multi_pii-v1: Apache-2.0 (snippet); EN/FR/ES/DE/IT/PT; F1/latency UNVERIFIED.
- knowledgator/gliner-pii-{edge,small,base,large}-v1.0: license UNVERIFIED. Snippet F1: 75.50 / 76.84 / 80.99 / 83.25. CPU latency UNVERIFIED (only "gline-rs 4x faster than Python on CPU").
- nvidia/gliner-PII: license "other", NVIDIA Open Model License (not Apache). Benchmarks UNVERIFIED.
- GLiNER2-PII (arXiv 2605.09973, Fastino): reported Apache-2.0 (snippet). GLiNER library: Apache-2.0.
- URLs: https://github.com/urchade/GLiNER, https://huggingface.co/knowledgator/gliner-pii-large-v1.0, https://huggingface.co/nvidia/gliner-PII, https://fastino.ai/blog/gliner2-pii-open-source-privacy-filtering-with-pii-detection

**5. Portkey.** TypeScript, plugin architecture, "50+ guardrails" on input and output. Page claims streaming guardrail support; specifics UNVERIFIED.

**6. LiteLLM Presidio guardrail**
- `output_parse_pii: true` does output de-masking as a literal string replace against a per-request table; placeholders like `<PERSON_1>` restart each request.
- Issue #31950 (open, Jul 2026): placeholders not restored in `tool_calls[].function.arguments`.
- Issue #30728: guardrail fails open on analyzer error.
- Streaming: `/v1/messages` output masking skipped; tokenizer splits placeholders across frames; PRs #42351, #43345 (Gemini SSE buffering); issue #42476 (Anthropic passthrough streams bypass post-call hooks). Seen from search results, not run.
- Which Presidio features are enterprise-licensed: UNVERIFIED.
- https://github.com/BerriAI/litellm/issues/31950

**7.** Envoy AI Gateway is now branded "Agent Router" (Agentic AI Foundation); CRDs, images, Go module path unchanged; no OSS PII plugin found (secondary sources only). Kong AI PII Sanitizer: Enterprise-only, Gateway 3.10+, requires AI Proxy/AI Proxy Advanced, self-hosted container.

**8. NeMo Guardrails.** Input, output and retrieval sensitive-data rails on Presidio (needs spaCy en_core_web_lg). Detect and mask; no reversible restore seen (UNVERIFIED). https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/third-party/presidio

**9. OPA.** REST API, Go SDK (embed in Go) and WASM. WASM usable from Python: UNVERIFIED. Typical decision latency: UNVERIFIED. v1.21.1 fixes a compiler regression in v1.21.0 (`some ... in`/`every` in nested comprehensions); avoid v1.21.0.

**10. cryptography.** `AESSIV` since 37.0.0 (source docs on raw.githubusercontent.com). Keys 256/384/512 bits. `encrypt(data, associated_data)` / `decrypt(...)`, associated_data is a list of bytes, no nonce arg; last AAD item acts as nonce in nonce-based mode. `*_into` variants since 47.0.0. Deterministic mode leaks equality of plaintexts.

**11. carnaval.** Real but tiny: 1 star, v0.2.3, 187 tests, regex + denylist + GLiNER, AES-256-GCM, 6 languages. "Enterprise production use" claim unverified.

**12.** truefoundry template: FastAPI app for TrueFoundry's custom-guardrail contract (Presidio redaction, NSFW, Guardrails AI). guardrails_pii: "This repository is archived and no longer maintained"; Presidio + GLiNER validator.

**13. Newer reversible-PII OSS** (star counts other than Anonproxy and last-commit dates not verified)
- https://github.com/francesco-stimola/llm-proxy-pii-rust: Rust, OpenAI-compatible + Anthropic, reversible `[EMAIL_1]`, fail-closed. AGPL-3.0 + commercial. ~20 ms/request structured-only; ~4.7 s/turn with NER (~563 MB). Streaming listed in search result, not confirmed on page.
- https://github.com/jfreemansh/Anonproxy: MIT, 7 stars, 57 commits; tolerant restoration, reassembles surrogates split across stream chunks; regex + optional GLiNER/Ollama/Piiranha.
- Snippets only, not opened: akazah/prompt-anonymizer, zeroc00I/DontFeedTheAI, Cloakport, Shroud. List: https://github.com/malteos/awesome-anonymization-for-llms

## Corrections to the source report

- Presidio repo and images moved: `data-privacy-stack/presidio`, `ghcr.io/data-privacy-stack/presidio-*`; MCR images no longer updated.
- guardrails_pii is ARCHIVED; the source report recommends it.
- llm-guard archive (9 Jul 2026) confirmed.
- nvidia/gliner-PII is not Apache-licensed (NVIDIA Open Model License).
- carnaval has ~1 star.
- LiteLLM Presidio guardrail known issues: #30728 (fail-open on analyzer error); #31950 (tool_calls not restored); streaming split-placeholder leak, cited as #41611 by the coordinator. I could NOT verify #41611 or that its fix buffers the full stream (UNVERIFIED). Verified streaming items are PRs #42351 and #43345 and issue #42476; #41600 is a feature request, not the leak.

## Adoption decision

| Component | Decision | Reason |
|---|---|---|
| Presidio | ADOPT | Analyzer as one detector; pin version; use ghcr images. |
| GLiNER library | ADOPT | Model choice pending license verification + E-DET benchmark. Prefer Apache: urchade/gliner_multi_pii-v1, knowledgator if Apache, GLiNER2-PII. |
| cryptography AESSIV | ADOPT | Present since 37.0.0; deterministic tokens (equality leak noted). |
| OPA | REFERENCE ONLY | Phase 1 per ADR-0006 (in-process decision tables); revisit for tool-gateway authorisation in Phase 5. |
| LiteLLM | REJECT in front of core | Allowed behind core as provider router only after supply-chain review; de-masking bugs. |
| Portkey | REFERENCE ONLY | TypeScript; architecture reference. |
| llm-guard / guardrails_pii | REJECT | Archived. |
| carnaval, Anonproxy | REFERENCE ONLY | Tiny projects; design references. |
| llm-proxy-pii-rust | REJECT | AGPL-3.0. |
| Kong AI sanitizer | REJECT | Enterprise-only license. |
| NeMo Guardrails | REFERENCE ONLY | No reversible restore. |
