# Risk Register

- **Status:** Proposed (Phase 0)
- **Date:** 2026-09-30

L = likelihood (1–5), I = impact (1–5), Sev = L×I. Project and delivery risks
are listed alongside technical ones because they are the ones most likely to
kill this.

| ID | Risk | L | I | Sev | Detection | Mitigation / gate |
|---|---|---|---|---|---|---|
| R-01 | **Scope creep**: building vault, RAG, agents, console before the core proves recall. | 5 | 5 | 25 | Phase gate reviews | ROADMAP phase gates; "not in Phase 1" list is binding. |
| R-02 | **No real use case / owner**: platform built in search of a problem; no app adopts it. | 4 | 5 | 20 | No pilot app at Phase 0 exit | Phase 0 exit requires a named owner + pilot app. |
| R-03 | **Synthetic-data overfitting**: gates pass on synthetic data, recall on real HR text is much lower. | 4 | 5 | 20 | E-DET-R spot check | Diverse generators, names outside EDM, E-DET-R inside client boundary, conservative thresholds for RESTRICTED. |
| R-04 | **False sense of safety**: stakeholders read "gateway" as "guaranteed anonymous" and relax other controls. | 4 | 5 | 20 | Review of onboarding docs | DESIGN §9 limitations in every onboarding; per-type recall published; legal treats output as personal data. |
| R-05 | **Implementation-bug leak** (offsets, retries, unhandled content field). | 3 | 5 | 15 | Egress invariant, E-EGRESS-FUZZ | ADR-0007; reject unknown request shapes. |
| R-06 | **Latency kills adoption**, especially NER on long histories. | 4 | 3 | 12 | E-PERF | Detection cache, NER only on free text, ONNX int8, GPU pool option. |
| R-07 | **Utility loss** makes app teams bypass or demand ALLOW everywhere. | 3 | 4 | 12 | E-UTIL | Per-purpose GENERALIZE; E-FMT format choice; policy relax only via reviewed pack change. |
| R-08 | **Bypass**: an app obtains provider keys and calls directly. | 3 | 5 | 15 | Provider-side key inventory; egress logs | Only gateway holds keys; network egress policy; periodic key rotation. |
| R-09 | **Supply-chain compromise** of a Python dependency or model weights in the plaintext path. | 2 | 5 | 10 | SBOM scan, hash pinning | Minimal deps, `--require-hashes`, no outbound network from NER pool, weights checksummed. |
| R-10 | **Fail-closed outages** anger users; pressure to add fail-open. | 3 | 3 | 9 | Block-rate metrics | Agreed SLO in onboarding; no fail-open code path exists for RESTRICTED. |
| R-11 | **Provider protocol drift** (new fields, new streaming events) creates un-inspected content paths. | 4 | 4 | 16 | Unknown-field rejects in metrics | Strict parsing, reject unknown content fields, golden tests per provider version. |
| R-12 | **Key compromise** enables alias→value confirmation for low-entropy values. | 2 | 4 | 8 | KMS audit logs | Per-tenant keys, rotation, KMS in P3, scope-limited aliases. |
| R-13 | **Legal misclassification**: assuming pseudonymised data is out of DPDP scope. | 3 | 4 | 12 | Privacy office review | Treat all egress as personal data; processor contracts with providers. |
| R-14 | **Team capacity**: one person carrying an enterprise-grade system alongside other duties. | 4 | 4 | 16 | Missed phase estimates | Phase 1 is sized to be shippable by 2 people in ~2 months; cut scope, not gates. |
