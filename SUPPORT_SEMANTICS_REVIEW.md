# SUPPORT SEMANTICS REVIEW

**Status:** Research. Written before implementation. **Date:** 2026-09-22

## 0. What Gate 1.75 established

| Finding | Status |
|---|---|
| Literal constraints defeat lexically-overlapping negatives (class C 1.000 → 0.000) | **useful, keep** |
| Localized evidence is enforceable (unsupported exposure = 0 across three TEST runs) | **works, keep** |
| Lexical anchoring is brittle (false support 0.25–0.50) | **not viable as the core criterion** |
| Paraphrase breaks lexical retrieval (zero-overlap R@10 = 0.1) | **measured** |
| Candidate-level lexical support can validate the *wrong artifact* (`apac` in `routing.xml` for a `fees.csv` question) | **disqualifying for S-A** |

The last row is the decisive one. It is not a tuning failure: the gate asked
*"does this chunk share distinguishing vocabulary with the query?"* and got a
truthful "yes" about a chunk that does not answer the question. **A question
about a property was validated against evidence for a different property.**

## 1. The architectural question, stated precisely

Two different propositions can be called "support":

> **(P1) Entailment.** *This text semantically entails this natural-language question's answer.*
>
> **(P2) Claim correspondence.** *This question maps to a compiled claim about a known entity/property/relationship, and that claim carries verified evidence.*

These are not the same problem. P1 is open-ended natural-language inference. P2
is a lookup against a structure the compiler already built, plus the evidence
check this project already enforces at the database level.

**Gate 1.75 tried to approximate P1 with lexical heuristics.** That is what
produced a 0.25 false-support rate and the wrong-artifact failure. Approximating
entailment with term overlap is precisely the "inferring semantic truth from
lexical overlap" the correction warns against.

The claim of this review: **for the question classes this compiler actually
compiles, P2 is sufficient and P1 is unnecessary.** Where P2 does not apply, the
honest outcome is `ABSTAIN`, not a heuristic guess.

## 2. Designs compared

### S-A — lexical gate (current)
*Evidence span + distinctive terms + literals.*

| | |
|---|---|
| **Can prove** | the cited span exists verbatim at a pinned hash; a queried literal is present in that span |
| **Cannot prove** | that the span concerns the *property* asked about; that the span is from the *right artifact*; anything about a paraphrased query with no shared vocabulary |
| **Failure mode** | validates a real span for the wrong question (measured) |
| **Deterministic** | yes |
| **Verdict** | keep the literal and evidence conditions; **reject term distinctiveness as the core criterion** |

### S-B — typed query constraints
*Compile the query into explicit fields — entity, property, relationship, literal, source_scope, temporal qualifier, operator — then check a compiled claim against them.*

| | |
|---|---|
| **Can prove** | that a claim exists whose subject = the named entity and predicate = the named property, with verified evidence; and that a stated literal matches the claim's value |
| **Cannot prove** | anything when the query does not parse into constraints; anything about intent or rationale expressed only in prose |
| **Failure mode** | constraint extraction fails or mis-parses → must abstain, not guess |
| **Deterministic** | **the checking is; the extraction is only partly so.** Identifier queries, `what is X's Y`, and `does A call B` parse by rule. Open prose does not. |
| **Verdict** | **the right support criterion**, bounded by extraction coverage |

### S-C — claim-first retrieval
*Retrieve entities/claims, then the evidence attached to them; never treat an arbitrary chunk as an answer.*

| | |
|---|---|
| **Can prove** | the answer is a compiled claim with `establishment ∈ {DERIVED, CONFIRMED}` and `EXACT` evidence — the strongest guarantee available |
| **Cannot prove** | anything the compiler did not compile into a claim. Prose rationale ("why 30 seconds") is not a claim in the current model |
| **Failure mode** | silently narrow coverage: the system appears confident and complete while being blind to everything uncompiled |
| **Deterministic** | yes |
| **Verdict** | correct for compiled facts; **must be paired with an explicit statement of what is out of scope** |

### S-D — hybrid
*Lexical/dense retrieval finds candidates; the compiled claim graph and typed evidence decide support.*

| | |
|---|---|
| **Can prove** | everything S-C proves, over a wider candidate set |
| **Cannot prove** | more than S-C — **retrieval breadth does not add proving power** |
| **Failure mode** | the one to guard against: letting retrieval rank leak into the support decision, which is how S-A failed |
| **Deterministic** | the support layer is, regardless of how candidates were found |
| **Verdict** | **the likely destination**, provided the support layer never reads the retrieval score |

## 3. The distinction that must not be blurred

> **Retrieval finds plausible evidence. It does not prove the evidence answers
> the question.**

Concretely, the layers get different inputs:

```
retrieval      sees: query text, index          → ordered candidates
support        sees: query CONSTRAINTS, claims, evidence → decision
```

The support layer must **not** receive the retrieval score, the rank, or the
lexical overlap. Gate 1.75's gate received all three and used them as evidence of
support. A candidate's provenance is irrelevant to whether it is supported.

## 4. Where semantic inference becomes unavoidable

Deterministic constraint extraction covers, by rule:

| Query shape | Example | Extractable |
|---|---|---|
| bare identifier | `SignatureExpired` | entity |
| property of entity | "what is the default salt of Signer" | entity + property |
| relation between entities | "does Serializer call want_bytes" | entity + relation + entity |
| literal assertion | "is the timeout 90 seconds" | entity + property + literal |
| scope qualifier | "where in signer.py is …" | + source_scope |

It does **not** cover: "why was this chosen", "what happens if this fails",
"how does signing work" — questions whose answers are prose rationale rather than
a compiled claim.

**So the deterministic boundary is exactly: questions that map to a compiled
claim.** Outside it, the deterministic system must abstain. That abstention is a
correct answer, not a gap to paper over — and it is where an LLM may later help
**interpret the query into constraints**, never verify the evidence.

## 5. What this review proposes to measure

Not a preference — a measurement. The claim-first hypothesis is testable:

> For each answerable query, does a compiled claim exist that corresponds to the
> requested information, and can it be located?

If claim-first coverage is high on compiled-fact queries and its support decision
is safe on hard negatives, S-C/S-D wins and S-A's term distinctiveness is
deleted. If claim coverage is low, the current claim model is insufficient and
that is the finding.

Measured in `experiments/claim_first_eval.py` against a corpus this project did
not author.
