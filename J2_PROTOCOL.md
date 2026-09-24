# J-2 PROTOCOL — REAL-MODEL SEMANTIC ENTAILMENT

What was actually done, in the order it was actually done. Every ordering claim
below is checkable against git history, because the artefacts were committed as
they were produced rather than assembled afterwards.

---

## 1. System under test — frozen

    compiler commit        7bc625319922393bd62313c2a47b845d1aa94616
    schema version         1
    demo version           0.2.0

Not modified for this evaluation: the answer validator (`kgq/validate.py`), the
evidence verifier (`kgc/evidence.py`), the deterministic structural checks, the
identity rules (`kgq/retrieval.resolve_identity`), retrieval, or claim semantics
(`kgc/predicates.py`).

**Two changes were made outside the system's measured behaviour.** The first,
under the §1 exception, is recorded separately in
`eval/j2/DEFECT_001_user_agent.md` **before** it was applied: `kgq/provider.py`
sent urllib's default `User-Agent`, which Groq's edge rejects with Cloudflare
1010 → HTTP 403. That defect made the first run abstain 48/48 without reaching
the model. It touches transport only. That run was discarded, not reported.
The second is the harness checkpointing described in §9a, which is execution
durability in the runner and touches nothing the system does.

Two earlier runs are discarded and must not be used: the 403 User-Agent run,
and `eval/j2/DISCARDED_run_429_rate_limited.json`, in which 43 of 48 questions
abstained because Groq returned HTTP 429. Neither is merged into any reported
result.

## 2. Corpus — fresh database

    repository             https://github.com/pallets/werkzeug
    commit                 6048fa48753c7b61e35cc34537667809dee8fa35
    corpus content sha256  19f4028a0a9f15b4e77d41ad489ee3368b72b309c9ac2836b1d34941fc32d35f
    artifacts / symbols    225 / 6,102
    claims / evidence      16,244 / 16,066
    invariant violations   []

Two databases were built from scratch for the two runs (§16). Neither contains
any previous experiment.

## 3. Query set — independently authored, frozen

    eval/j2/J2_QUERIES.json
    sha256   977ca0761b769c8797c471b035ad735022295bcf2175446fd6bd843a56b1a713
    count    48  (J2-001 … J2-048)

Written by an authoring agent that read only `eval/corpus3`. Independence was
verified **first-hand** by reading that agent's own tool-call log — 45 calls,
all under the corpus — not by accepting its report. One leak is recorded: a
`find eval/` printed the *filenames* in `eval/`, which include `J1_GOLD.json`;
no content was opened.

The file carries `id` and `question` only. No categories, no difficulty labels,
no answerability hints, no expected answers.

### Stratification against J-1, registered before gold existed

J-1 is burned *on the system side*: the Demo 0.1 retrieval fix came from J1-006
and the flagship demo question is J1-009's subject. Ten J-2 questions ask a fact
a named J-1 question already asked and fourteen more are adjacent.

**No question was removed or edited** — deleting questions after reading them is
the tuning §18 forbids. They are stratified (`eval/j2/J2_QUERY_PROVENANCE.json`)
so results report separately:

    FRESH                24   primary denominator
    J1_OVERLAP_PARTIAL   14   reported separately
    J1_OVERLAP_STRONG    10   reported separately

The primary denominator is therefore 24, below the 36–50 the brief targets. That
cost is stated here rather than discovered after the numbers land.

## 4. Gold — established from source, before any run

    eval/j2/J2_GOLD.json
    sha256   4e6b39b36a2ce1719d674e038e04fa0f0591425d36d4270c9091b7cfd7cb873a

Authored by reading `eval/corpus3` directly. **Committed before the runner
existed and before any model call**, so the ordering §4 requires is provable
from git history rather than asserted.

Each entry carries answerability, the intended entity, and for every established
fact a source path, a locator and a verbatim excerpt. Each PARTIAL and
UNANSWERABLE entry carries a written reason the source cannot establish the
requested answer.

    ANSWERABLE     42
    PARTIAL         5   (J2-009, J2-025, J2-029, J2-046, J2-048)
    UNANSWERABLE    1   (J2-028)

## 5. Model — one real model, temperature 0

    provider     Groq (OpenAI-compatible), https://api.groq.com/openai/v1
    model        openai/gpt-oss-120b
    temperature  0.0
    max_tokens   1200 per call
    cache        disabled, so run 2 is an independent repeat and not a replay

No provider registry was created; the existing `kgq/provider.py` interface was
used unchanged apart from DEFECT-001.

### Harness pacing, which is not a system change

Groq's tier allows 8,000 tokens per minute per model. A J-2 answer prompt
carries up to 24,000 characters of evidence, so an unpaced run gets HTTP 429,
`ask()` reports "model unavailable" and abstains — which would measure a billing
tier, not a semantic layer. `eval/j2/run_j2.py` wraps the provider in a
`PacedProvider` that only delays and retries transport. It alters no message,
temperature, budget, retrieval result or decision. Latency is reported from
`Provider`'s own measured API time and **excludes every pacing sleep**.

## 6. Adjudication — §7, §9

Every exposed answer is decomposed into its material statements. Each statement
is judged independently as SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED /
CONTRADICTED / NOT_DETERMINABLE. The five are never collapsed into "grounded".

**The generator is never a judge.** Judges are different models, on a different
bucket, and receive only the question, one statement, and the cited source text
re-read from disk. They never receive the system's status, its validation
outcome, its structural-support labels, the gold file, or each other's verdicts.

Two judges run independently; **disagreement is reported, never silently
resolved**. Where they split, the conservative reading takes the worse verdict,
so a disagreement can never flatter the system. A sample is adjudicated by hand
to calibrate the judges.

Results carrying model judgement are labelled **LLM-adjudicated, not ground
truth**, everywhere they appear.

## 7. Metrics — §10, reported separately, no overall score

Semantic entailment precision; answer-level full support; **grounded-but-wrong**
(cited evidence existed, was retrieved, and still matched the source bytes, yet
the statement is not entailed); unsafe exposure on questions the source cannot
establish; appropriate abstention; useful-answer availability. Structural checks
are counted separately (§11), and deterministic evidence integrity is reported
separately from entailment (§12) — the first is grounding and is never counted
as the second.

## 8. Reproducibility — §16

Two runs, two fresh databases, identical model configuration, query hash, gold
hash and corpus hash recorded in each result file. Decision, evidence ids and
structural checks are compared across runs. Model nondeterminism, if observable
at temperature 0, is reported rather than hidden.

## 9. Demo regression set — §15, never merged

`eval/j2/J2_DEMO_TARGETS.json` holds the Demo 0.1/0.2 target questions,
including the flagship. The system was developed against them, so they measure
regression, not generalisation. They run separately and are reported separately.

## 9a. Harness amendment — per-question atomic checkpointing

**Execution durability only. No system-under-test behaviour changed. No frozen
evaluation input changed.**

The runner originally wrote its result file once, after all 48 questions. An
interruption therefore lost every completed in-memory result — a harness
reliability problem, not a system result.

`eval/j2/run_j2.py` now persists a checkpoint after **every** completed
question, via write-to-temp → `fsync` → `os.replace`, so a killed process can
never publish a half-written file. Each checkpoint carries the run label, the
query/gold/corpus SHA-256s, the provider and model configuration, the frozen
system-under-test identifier (compiler commit, schema version, demo version),
the completed question ids, their results, cumulative usage, pacing seconds,
rate-limit retries and a timestamp.

`--resume` reloads a checkpoint and **refuses** unless the run label, query
hash, gold hash, corpus hash, database, provider, model, temperature, budget
and system-under-test identifier all match; it then skips completed ids and
continues. Running over an existing checkpoint *without* `--resume` refuses
rather than silently restarting from question 1. Completed results are never
recomputed or altered on resume, and the final artifact holds all 48 results
exactly once, in frozen query order.

The database file's own hash is deliberately not a resume gate: the retriever
builds its FTS index on first use, so the file legitimately changes during a
run. The corpus content hash is what pins what was compiled.

Verified by `eval/j2/test_harness_resume.py` against a deterministic **mock
transport** — 32 checks covering survival of `SIGKILL`, both refusal paths, no
re-asking, no alteration of completed results, and exactly 48 unique results.
**That mock run is harness testing and is not a J-2 result.**

## 10. No tuning on test — §18

After the queries and gold were frozen: no query-specific parsing rule, no
per-question prompt change, no per-question retrieval change, no claim-semantics
change, no per-question validation change. The only code change after freezing
is DEFECT-001, which is transport and is recorded above.
