"""The answer path, end to end, with an explicit budget.

    question
      -> interpretation            (model-assisted, UNTRUSTED)
      -> deterministic retrieval    (identifier, graph hop, FTS5)
      -> evidence set               (existing ids only)
      -> semantic reasoning         (model, constrained to those spans)
      -> structured candidate       (contract.ModelAnswer)
      -> deterministic validation   (validate.Validator)
      -> ANSWER (PROPOSED) or ABSTENTION

A rejected candidate is regenerated ONCE with the validator's reason handed
back, then the system abstains. A statement is never silently dropped: dropping
one leaves a shorter answer that looks fully supported, which is the failure
this gate exists to prevent.

The answer is a query-time observation with provenance. Nothing here writes a
claim: the semantic layer never touches the graph.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from kgq.contract import (ContractError, Intent, answer_messages, compose_answer,
                          interpret_messages, parse_answer, parse_intent)
from kgq.provider import ProviderError, Usage
from kgq.retrieval import Retriever, candidate_identifiers
from kgq.validate import ACCEPTED, EXPLICIT_CONTRADICTION, Validator

ANSWER = "ANSWER"
ABSTAIN = "ABSTAIN"
ABSTAIN_AMBIGUOUS = "ABSTAIN_AMBIGUOUS"
PROPOSED = "PROPOSED"          # what a semantic answer always is
DERIVED = "DERIVED"            # what the structural facts beside it always are


@dataclass
class Budget:
    """Bounded, configurable, and recorded. Not a promise about cost."""
    interpretation_attempts: int = 1
    answer_attempts: int = 2
    max_context_chars: int = 24000
    max_spans: int = 6

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Result:
    question: str
    status: str                                  # ANSWER | ABSTAIN | ABSTAIN_AMBIGUOUS
    answer: str = ""
    abstain_reason: str = ""
    intent: dict = field(default_factory=dict)
    subjects: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    structural_facts: list = field(default_factory=list)
    claims: list = field(default_factory=list)
    validation: dict = field(default_factory=dict)
    attempts: int = 0
    regenerations: int = 0
    usage: dict = field(default_factory=dict)
    budget: dict = field(default_factory=dict)
    latency_seconds: float = 0.0
    establishment: str = PROPOSED
    structural_status: str = ""
    identity: dict = field(default_factory=dict)
    model_prose_unvalidated: str = ""            # recorded, never shown as the answer

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def interpret(question: str, provider, budget: Budget) -> Intent:
    """A model reads the sentence. Its output is a hypothesis, nothing more.

    Falls back to deterministic identifier extraction when no model is
    configured or the model misbehaves -- the system must degrade, not break.
    """
    fallback = Intent(subject=(candidate_identifiers(question) or [None])[0],
                      requested_fact=question, answer_type="unknown",
                      search_terms=candidate_identifiers(question),
                      source="deterministic")
    if provider is None or budget.interpretation_attempts < 1:
        return fallback
    try:
        body = provider.chat(interpret_messages(question), max_tokens=300)
        intent = parse_intent(body)
        if not intent.subject:
            intent.subject = fallback.subject
        return intent
    except (ProviderError, ContractError):
        return fallback


def ask(question: str, *, db_path: str, corpus_root: str, provider=None,
        budget: Budget | None = None) -> Result:
    budget = budget or Budget()
    t0 = time.perf_counter()
    r = Result(question=question, status=ABSTAIN, budget=budget.as_dict())
    retriever = Retriever(db_path, corpus_root)
    try:
        intent = interpret(question, provider, budget)
        r.intent = intent.as_dict()

        # IDENTITY FIRST. A model may decide which word the question is about;
        # it may not decide which `Response` that word means. If the compiled
        # graph says the name is ambiguous and nothing deterministic narrows it,
        # we stop here -- before retrieval puts several candidates in front of a
        # model that would quietly pick one.
        identity = retriever.resolve_identity(question, subject_hint=intent.subject)
        r.identity = identity
        if identity["ambiguous"]:
            a = identity["ambiguous"][0]
            r.status = ABSTAIN_AMBIGUOUS
            r.abstain_reason = (
                f"{a['name']!r} resolves to {len(a['candidates'])} symbols "
                f"({', '.join(a['candidates'][:3])}"
                f"{'...' if len(a['candidates']) > 3 else ''}) and nothing in the question "
                f"picks one. Name it in full, or add the file it is in.")
            return _finish(r, provider, t0)

        spans, subjects = retriever.retrieve(
            question, subject_hint=intent.subject,
            max_chars=budget.max_context_chars, limit=budget.max_spans)
        r.evidence = [sp.as_dict() | {"excerpt": sp.text} for sp in spans]
        r.subjects = [{"qualified_name": s["qualified_name"], "kind": s["kind"],
                       "rel_path": s["rel_path"]} for s in subjects]

        facts = []
        for s in subjects[:2]:
            facts += retriever.structural_facts(s["symbol_id"])
        # one row per distinct edge; the same call site can appear twice
        seen, uniq = set(), []
        for f in facts:
            k = (f.predicate, f.subject, f.object)
            if k not in seen:
                seen.add(k); uniq.append(f)
        r.structural_facts = [f.as_dict() for f in uniq]

        if not spans:
            r.abstain_reason = ("no source evidence could be retrieved for this question; "
                                "nothing in the compiled corpus matched it")
            return _finish(r, provider, t0)
        if provider is None:
            r.abstain_reason = ("no model configured, so no semantic answer was attempted. "
                                "The deterministic evidence and structural facts above were "
                                "produced without one.")
            return _finish(r, provider, t0)

        retrieved_ids = {sp.evidence_id for sp in spans}
        validator = Validator(retriever.con, corpus_root)
        reason = None
        for attempt in range(1, budget.answer_attempts + 1):
            r.attempts = attempt
            try:
                body = provider.chat(answer_messages(question, spans, uniq, reason))
                candidate = parse_answer(body)
            except ContractError as e:
                reason = f"your reply did not satisfy the output contract: {e}"
                r.validation = {"outcome": "REJECTED", "reason": reason, "claims": []}
                continue
            except ProviderError as e:
                r.abstain_reason = f"model unavailable: {e}"
                return _finish(r, provider, t0)

            verdict = validator.validate(candidate, retrieved_ids)
            r.validation = verdict.as_dict()
            r.claims = [c.as_dict() for c in candidate.claims]
            contradictions = verdict.contradictions
            r.structural_status = (EXPLICIT_CONTRADICTION if contradictions else
                                   "CHECKED" if any(c.structural for c in verdict.claims)
                                   else "NOT_APPLICABLE")
            if verdict.outcome == ACCEPTED:
                # The reader sees text assembled from VALIDATED claims only. The
                # model's own prose is kept for the record and never rendered as
                # the answer, so an unvalidated sentence has no path to a user.
                r.status = ANSWER
                r.answer = compose_answer([c.text for c in candidate.claims])
                r.model_prose_unvalidated = candidate.answer
                return _finish(r, provider, t0)
            reason = verdict.reason()
            if attempt < budget.answer_attempts:
                r.regenerations += 1

        r.abstain_reason = (f"the candidate answer failed deterministic validation "
                            f"{r.attempts} times and was not shown. Last reason: {reason}")
        return _finish(r, provider, t0)
    finally:
        retriever.close()


def _finish(r: Result, provider, t0: float) -> Result:
    usage = Usage()
    if provider is not None and hasattr(provider, "usage"):
        usage.add(provider.usage)
    r.latency_seconds = round(time.perf_counter() - t0, 3)
    r.usage = usage.as_dict() | {"regenerations": r.regenerations}
    return r
