# J-1 PROTOCOL — Independent Query Reality Test

**Pre-registered 2026-09-23, before any query was seen and before any measurement was run.**

J-1 does not ask whether the implementation is correct. Six gates already asked
that. J-1 asks whether an independent user gets value from it. A contaminated
result is worse than a bad one, so every rule below exists to stop me from
steering the outcome.

---

## 1. Substrate (frozen before authoring)

`eval/j1/J1_SUBSTRATE.json`, built from an empty database:

| | |
|---|---|
| corpus | `eval/corpus3` — Werkzeug @ `6048fa48753c7b61e35cc34537667809dee8fa35` |
| corpus content hash (sha256 over every file's path + digest) | `9a7ca7ce517fbd1c09d3379f709172656722350b65cb5a3e88d100599330c90c` |
| files / analysable | 225 / 140 |
| artifacts: OK / UNSUPPORTED / FAILED / absent | 140 / 85 / 0 / 0 |
| symbols / claims | 6,102 / 16,244 |
| predicates | CALLS 8,375 · CONTAINS 5,962 · IMPORTS 1,582 · EXTENDS 163 · HAS_VALUE 162 |
| invariants | `[]` |
| compiler commit | `ed2fdfe` |
| software / schema / backend / resolver | `0.1.0-gate1` / `1` / `python_ast/1.0.0` / `1.0.0` |

A fresh database is mandatory: the compiler is append-only across corpus
revisions (K-1.2 §10), so a reused database could answer from stale artifact
versions. That architecture is **not** changed during J-1.

## 2. Query authoring — independence

The query set is authored by a separate agent that starts with no knowledge of
this session and is permitted to read **only** `eval/corpus3/`. It is explicitly
forbidden from reading `kgc/`, `experiments/`, the repository-root `tests/`,
`eval/*.json`, and every root `.md` file — i.e. the extractor, the query grammar,
`decide()`, the retrieval code, all gold files and all previous results.

It is told exactly the wording this gate specifies:

> Read this repository as a user. Write natural questions whose answers you
> would reasonably expect a repository knowledge system to provide. Include both
> questions that should be answerable from the repository and questions where a
> careful system should say it cannot establish the answer.

It is given **no categories**, no templates, no expected decisions, and is told
not to label, group, tag or sort the questions. Output is `id` + `question` only.

**Stated limitation, up front:** this author is a language model instance, not a
human developer. It shares a training distribution with me, so its phrasing may
correlate with mine in ways a real user's would not. It is isolated from the
system under test, which is the contamination that matters most, but this is an
approximation of independence and the report will say so rather than claim more.

Frozen as `eval/J1_INDEPENDENT_QUERIES.json`, **54 questions**, SHA-256
`fac1e7ec10f34498b75d6563be3cd827f7f208f7db4abd5fef1375014a1e2083`.
**Query text is never edited after freezing**, and none was.

**Disclosed contamination risk.** On finishing, the author volunteered an
unprompted summary of roughly how many of its questions it believed answerable,
unsupported or ambiguous, and named some of them. I read that before labelling
gold. I did not use it: every gold label below is derived from the Werkzeug
source with the file, line and verbatim text recorded, so each one is auditable
against the corpus rather than taken on anyone's word. The risk is recorded here
rather than hidden, and the audit trail is what defends against it.

## 3. Gold labelling — method and its access

Gold is established by me (the system author) **from the Werkzeug source only**,
after the queries are frozen and before the evaluation harness is run against
them. I may read `eval/corpus3/`. I may not consult `decide()` output, the claim
database, or any system result while labelling.

Each query gets one label:

| label | meaning |
|---|---|
| `answerable` | the requested fact is determinable from this corpus's source |
| `unsupported` | the corpus cannot establish it (runtime, deployment, intent, another library, simply absent) |
| `ambiguous` | the question names something that exists in more than one place here, with no disambiguator |
| `out_of_scope` | the question is not about this corpus at all |

For `answerable`: record the intended answer **and exact supporting evidence** —
file, line, and the verbatim source text — so the label is auditable rather than
asserted. For `unsupported`: record *why* the corpus cannot prove it. For
`ambiguous`: record the competing entities.

Frozen as `eval/J1_GOLD.json` with its SHA-256 recorded. **Gold is never revised
after the first evaluation run.** If labelling turns out to be wrong, that is
reported as a labelling error, not silently corrected.

## 4. The system is frozen

No change during J-1 to extraction, predicate vocabulary, query grammar, trust
boundary, entity resolution, evidence model, ranking or storage. No special case
is added to make a J-1 query parse. No synonym is added for any J-1 question. An
unparseable query is a recorded result, not a bug to fix.

Only two changes are permitted, and both must be documented separately before
being made: a harness bug that prevents the experiment running, and a
measurement or reporting error.

## 5. Measurements, defined now

### A. Parse availability

`extract()` yields `PARSED` / `PARTIAL` / `UNPARSEABLE` / `AMBIGUOUS`. Reported
as counts, plus:

* `full_parse_rate` = PARSED ÷ all queries
* `full_parse_rate_answerable` = PARSED ÷ answerable queries — **the number
  comparable to the 0.50 baseline**
* `parser_caused_abstention` = answerable queries abstained with
  `failed_invariant == INV-1_constraints` ÷ answerable queries

Baseline for comparison is the **hand-written 0.50** (Gate 2, corpus2,
author-written queries). The Gate 2.25 figure of 0.984 is *not* a baseline: those
queries were built from the same templates the parser recognises, so it measures
self-consistency. The report states this explicitly.

### B. Safety — on `unsupported`, `ambiguous` and `out_of_scope` questions

* `false_support` = outcome ∈ {EXPOSE, EXPOSE_CONFLICTED} ÷ these questions
* `unsafe_exposure` = exposed with any hit whose establishment is outside
  {DERIVED, CONFIRMED}
* `incorrect_resolution` = an `ambiguous` question answered with one reading
  instead of `ABSTAIN_AMBIGUOUS`
* `incorrect_confidence` = an exposed answer asserting a fact the cited evidence
  does not support (measured via §D, not self-reported — the system reports no
  confidence value)

The governing requirement is unchanged: **absence of evidence must never become
a supported answer.** The system is not tuned against J-1 to improve this.

### C. Utility — on `answerable` questions only

* `exposure_rate` = outcome == EXPOSE ÷ answerable
* `answer_correctness` = of those exposed, the fraction where the exposed claims
  **establish the specific requested relationship or value**
* `claim_correctness` = of those exposed, the fraction where every exposed claim
  is a true statement about the source

`answer_correctness` and `claim_correctness` are deliberately separate:
a claim can be true and useless. **Returning a related symbol is not success.**
Judged against the frozen gold answer, one query at a time, recorded per query.

### D. Evidence fidelity — every exposed answerable query

Verified against the **original corpus files on disk**, not the database's own
locator arithmetic and not the evidence-rank number:

* `evidence_localized` — the locator resolves to a real byte range in a real file
* `evidence_exact` — the bytes at that range equal the stored `quoted_text`
* `evidence_sufficient` — a reader shown only the cited bytes could confirm the
  answer. Judged per query and recorded.

## 6. Error analysis

Every failure is assigned exactly one root cause **after** evaluation, from:
`PARSER_GAP`, `CLAIM_VOCABULARY_GAP`, `IDENTITY_GAP`, `RETRIEVAL_GAP`,
`EVIDENCE_LOCALIZATION_GAP`, `AMBIGUITY_HANDLING`, `CORPUS_UNSUPPORTED`,
`IMPLEMENTATION_DEFECT`.

No engineering fix is designed during classification. The question this section
answers is a fork in the road, not a backlog: **can the compiler not represent
these facts, or does it hold them and fail to retrieve or interpret them?**

## 7. Claim-coverage table

For every answerable question: required semantic fact → is an existing predicate
sufficient (Y/N) → is the claim actually present (Y/N) → does the parser
represent the question (Y/N) → does the decision succeed (Y/N). This is the
empirical input to a future J-3 decision. **No predicate is added because a
question exposed a gap.**

## 8. Run control

J-1 is run **twice from two independently built fresh databases**. Same corpus,
same compiler, same frozen queries, same frozen gold. Decisions, hits and
evidence must be identical. Any difference is hidden state and is explained, not
averaged away.

## 9. Reporting

`J1_PROTOCOL.md` (this file, pre-registered), `J1_RESULTS.json`,
`J1_ERROR_ANALYSIS.md`, `J1_REPORT.md`.

**No single overall score.** The report answers five questions: can independent
users express useful questions; does the deterministic graph contain the
required facts; is safety preserved on unsupported questions; is the dominant
limitation parser, claim coverage or retrieval; and is this worth proceeding
with. A low number is a result, not a failure to manage.

## 10. Stop condition

J-1 stops at the report. The largest failure bucket is **not** implemented
afterwards. J-3 is not authorised by this gate.
