# GATE 2 REPORT — Support Semantics + Generalization

**Date:** 2026-09-22 · **Verdict: CONDITIONAL PASS**

The gate's central proposition is **confirmed**: evidence safety can be
guaranteed by verifying structured claims against deterministic evidence,
without the verifier performing any semantic inference. Claim-first support
achieved **0.000 false support on every split** of an independently authored
corpus, against chunk-first's 0.444 — including every hard-negative class that
defeated the Gate 1.75 gate.

The price is availability, and it is large: half of answerable queries abstain.

Tags: **[MEASURED]** · **[UNPROVEN]** · **[ASSUMED]** · **[BLOCKED]**.

---

## 1. The corpus is no longer mine

`eval/corpus2` — **itsdangerous @ `672971d66a2ef9f85151e53283113f33d642dabd`**,
BSD-3-Clause, 23 files, 70,767 bytes, corpus hash `1737e6c5…`. Written by the
Pallets project. Neither the code, the documentation, nor the facts the gold set
asserts were authored by this project.

Gold: 20 positives across 10 retrieval classes, 18 negatives across 7 support
classes, each positive carrying the compiled claim that should answer it.
Every gold evidence string was validated verbatim against the real source.
Splits stratified: CALIBRATION 13 / VALIDATION 14 / TEST 11.

## 2. Cross-module resolution did NOT generalize — until it was fixed

The Gate 1.5 result of 13/13 was, as suspected, a regression test. On real code
it collapsed:

| | Before fixes | After fixes |
|---|---|---|
| DETERMINISTIC | 3.2% | **18.4%** |
| UNRESOLVED | 96.8% | 70.0% |
| **Cross-file CALLS edges** | **0** | **38** |

Two defects, both invisible on synthetic corpora:

1. **Relative imports were ignored entirely.** `from .encoding import base64_decode`
   was read as a module named `encoding`, absent from the corpus. This is the
   dominant import style in real Python packages.
2. **Module naming was ingestion-root relative.** `src/itsdangerous/signer.py`
   was named `src.itsdangerous.signer` while its own imports say
   `itsdangerous.signer`, so every absolute import missed.

**Precision after the fixes: 1.000.** All 38 cross-file edges were verified
against an independent import scan of the raw source — a separate implementation,
not the extractor's own output. **Zero contradicted edges, zero
not-independently-checkable.** **[MEASURED]**

The remaining 70% unresolved is dominated by stdlib and typing imports
(`typing`, `__future__`, `collections.abc`) correctly reported as outside the
corpus, and by method calls on local variables.

## 3. Claim-first vs chunk-first

All 38 queries, identical corpus and gold:

| Split | Architecture | False support | False abstention | Doc R@10 | Evidence | Claim R@10 |
|---|---|---|---|---|---|---|
| CALIBRATION | chunk-first | 0.571 | 0.500 | 0.833 | 0.833 | — |
| | **claim-first** | **0.000** | 0.333 | 0.500 | 0.333 | 0.333 |
| VALIDATION | chunk-first | 0.500 | 0.250 | 0.875 | 0.875 | — |
| | **claim-first** | **0.000** | 0.625 | 0.375 | 0.250 | 0.375 |
| **TEST** | chunk-first | 0.200 | 0.167 | 1.000 | 1.000 | — |
| | **claim-first** | **0.000** | 0.500 | 0.500 | 0.500 | 0.333 |
| ALL | chunk-first | 0.444 | 0.300 | 0.900 | 0.900 | — |
| | **claim-first** | **0.000** | 0.500 | 0.450 | 0.350 | 0.350 |

**The two architectures are complementary, not competing.** Chunk-first is a
good retriever and a bad validator. Claim-first is the reverse.

### Claim-first catches exactly what killed the lexical gate **[MEASURED]**

| Negative | chunk-first | claim-first |
|---|---|---|
| `RedisSessionStore` (unsupported entity) | EXPOSE ✗ | ABSTAIN ✓ — not a compiled symbol |
| `JWTSigner` (unsupported entity) | EXPOSE ✗ | ABSTAIN ✓ |
| default salt of `BadSignature` (wrong property) | EXPOSE ✗ | ABSTAIN ✓ — no such property |
| `default_serializer` of `Signer` (near neighbour) | EXPOSE ✗ | ABSTAIN ✓ |
| salt of `HMACAlgorithm` (near neighbour) | EXPOSE ✗ | ABSTAIN ✓ |
| `default_digest_method` of `Serializer` (near neighbour) | EXPOSE ✗ | ABSTAIN ✓ |
| does `encoding.py` import `Serializer` (wrong relation) | EXPOSE_CONFLICTED ✗ | ABSTAIN ✓ |
| stale "still concat as in older versions" | EXPOSE_CONFLICTED ✗ | ABSTAIN ✓ |

Every one is a case where the query's vocabulary genuinely appears in a real
document, and the requested *fact* does not exist. No lexical signal can
separate these. **Asking the graph whether the claim exists separates all of
them.**

### Two bugs found and fixed during the experiment

- **Leaf symbols had no claims.** A constant or class attribute has no *outgoing*
  claims; its definition evidence sits on the incoming `CONTAINS` claim. Looking
  only outward made every constant look uncompiled.
- **Interrogative degradation.** A question that failed to yield a property fell
  back to bare-identifier lookup, so *"what port does TimestampSigner listen
  on"* returned `TimestampSigner`'s definition as though it were a port. An
  interrogative that does not parse now abstains.

Both were found by running the evaluation; the pre-fix run is preserved as
`experiments/claim_first_results_RUN1.json`.

## 4. S-D hybrid — the proposed architecture

Chunk retrieval supplies candidates; **the claim layer owns the support decision
and never sees the retrieval score, rank or lexical overlap.**

| Metric | Value |
|---|---|
| False support | **0.000** |
| False abstention | 0.500 |
| **Doc Recall@10 when it exposes** | **1.000** |
| Evidence correct | 0.500 |

When this architecture answers, it finds the right document **every time**. When
it cannot verify a claim, it says so.

## 5. Graph expansion — a Gate 1.5 finding REVERSED **[MEASURED]**

| Query class | lexical MRR | lexical + graph MRR | Δ |
|---|---|---|---|
| structural (n=9) | **0.755** | 0.518 | **−0.237** |
| semantic (n=11) | 0.639 | 0.652 | +0.013 |

Gate 1.5 measured graph expansion *helping* structural queries (0.75 → 1.00) on
my synthetic corpus. **On a real repository it hurts them substantially.** The
earlier benefit did not generalize.

I am not proposing a router on this evidence. The honest statement is that graph
expansion has **no demonstrated retrieval value on real code**, and the Gate 1.5
result should be treated as an artifact of a corpus I wrote.

## 6. Evidence localization — the Gate 1.75 gap did not reproduce **[MEASURED]**

Within-file localization was implemented and measured. On corpus2 it lifted
chunk-first evidence correctness from **0.900 to 0.900** — no change, because
chunk-first evidence was already 0.900 here.

The 19% gap measured in Gate 1.75 was a property of that corpus, not a general
defect. The mechanism is implemented and costs nothing; **no chunking change was
made**, and none is justified by this evidence.

## 7. Neural embeddings — **[BLOCKED]**, and the runner is now strict

Still no model: no `ollama`, no caches, no model files on disk, host unreachable.
**LSA was not substituted.**

Per item 11 the runner was hardened: an unattributable result is now
`validity.status = "INVALID"`, `decision.met` is forced to `false`, and the
process exits non-zero — it is no longer a warning that leaves a usable number
behind. The hashed-file list was corrected to what the ONNX runtime actually
downloads (`.onnx`, `.onnx_data`, `.safetensors`, `tokenizer.json`, …).

The guard is itself tested (`experiments/neural/test_validity_guard.py`): VALID
requires **both** a recorded revision and hashed files; all three failure
combinations force INVALID.

## 8. Answers

1. **Is lexical distinctiveness still needed in the core support decision?**
   **No.** It is removed. It produced 0.444 false support and validated the wrong
   artifact. Claim-first uses none of it and reaches 0.000. **[MEASURED]**
2. **What exactly constitutes a supported answer?** A compiled claim whose
   subject matches the query's entity, whose predicate matches the requested
   property or relation, whose stated literals appear in its evidence, whose
   evidence originates in the requested scope if one was given, and whose
   evidence carries `EXACT` or `REPRODUCIBLE` verification.
3. **Can a compiled claim be independently verified against evidence?** **Yes** —
   enforced at the database level since Gate 1, and 0 unsupported exposures
   across every run of every gate.
4. **Is claim-first superior to chunk-first support?** **For support, decisively
   yes** (0.000 vs 0.444 false support). **For retrieval, no** (doc recall 0.45
   vs 0.90). They are different jobs; §4 is the synthesis.
5. **Can structured constraints be extracted deterministically?** **For useful
   classes, yes: 0.70 of answerable queries parse** into entity/property/
   relation/literal/scope by rule alone. Identifier, property-of-entity and
   relation queries parse; prose does not. **[MEASURED]**
6. **Where does semantic inference become unavoidable?** At the query-to-
   constraint boundary, and nowhere else. The 30% that do not parse are
   rationale and paraphrase questions ("why should different salts be used").
   **Verification never needs inference.** An LLM may later interpret queries
   into constraints; it must never verify evidence.
7. **Does dense retrieval close the zero-overlap gap?** **[BLOCKED]** — unchanged.
8. **Does dense retrieval improve evidence retrieval?** **[BLOCKED]**.
9. **Does graph traversal provide measurable value on structural queries?**
   **No — it measurably hurts them** (−0.237 MRR) on a real repository,
   reversing Gate 1.5. **[MEASURED]**
10. **Does the resolver generalize?** **Only after two fixes.** It produced
    **zero** cross-file edges on real code before them; after, 38 edges at
    **1.000 independently verified precision**. **[MEASURED]**
11. **Is the current chunk/evidence model sufficient?** For *retrieval*, yes
    (doc recall 0.90). For *support*, no — chunk-level support is what the gate
    set out to test and it fails. Claims plus typed evidence are sufficient for
    support of compiled facts.
12. **Simplest architecture surviving the evidence?**
    ```
    query → constraint extraction (rule-based)
          → chunk/lexical retrieval for candidates
          → claim lookup + typed evidence verification   ← owns the decision
          → EXPOSE | EXPOSE_CONFLICTED | ABSTAIN
    ```
    No vector store, no graph expansion stage, no query router, no LLM.
13. **What remains unproven?** Whether a neural embedding closes the
    zero-overlap gap **[BLOCKED]**; whether claim coverage rises enough to make
    0.5 false abstention acceptable **[UNPROVEN]**; whether any of this holds on
    a corpus larger than 23 files **[ASSUMED]**.

## 9. Verdict: CONDITIONAL PASS

**Why not PASS.** Two reasons, both about sample size and coverage rather than
direction. TEST is 11 queries with 5 negatives — **0.000 false support on five
negatives is encouraging, not conclusive**, and I will not present it as proof.
And a system that abstains on half of what it could answer is not yet a usable
product, however safe.

**Why not FAIL.** The gate asked whether the support-gate architecture was
fundamentally the wrong abstraction. It was — and the right one is now
identified and measured on a corpus I did not write, against negatives that
defeated every previous design.

### Conditions

| # | Condition |
|---|---|
| **H-1** | Raise claim coverage. 0.500 false abstention is the binding limit; 0.30 of answerable queries do not parse into constraints at all. |
| **H-2** | Scale the corpus. 23 files and 38 queries cannot establish generalization; the next corpus should be an order of magnitude larger and again not authored here. |
| **H-3** | Compile rationale claims, or state permanently that "why" questions are out of deterministic scope. This is the single largest coverage gap. |
| **H-4** | Run the external neural experiment or close the question as unresolved. |
| **H-5** | Retire graph expansion as a retrieval stage unless a corpus shows positive Δ; the only two measurements disagree, and the one on real code is negative. |

## 10. Still prohibited

LLM in any role (including query interpretation, until the deterministic boundary
is accepted) · vector database · graph database migration · production query
router · UI · multimodal · provider adapters · distributed anything.

## 11. Reproducing

```bash
python3 experiments/validate_gate2_gold.py         # gold integrity
python3 experiments/c2_generalization.py           # §2, real repository
python3 experiments/claim_first_eval.py            # §3
python3 experiments/hybrid_and_graph_eval.py       # §4, §5
python3 experiments/neural/test_validity_guard.py  # §7
python3 -m unittest discover -s tests -t .         # 40 Gate 1 tests
```

Preserved, never rewritten: `experiments/x1_results.json`,
`abstention_results_RUN{1,2,3}*.json`, `claim_first_results_RUN1.json`.
