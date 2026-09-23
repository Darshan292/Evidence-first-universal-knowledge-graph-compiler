"""The deterministic gate. No model runs here.

It answers one question: may this candidate answer be shown at all? It verifies
GROUNDING -- that every material statement cites evidence that exists, was
retrieved for this question, and still matches the source bytes on disk.

It does NOT verify ENTAILMENT. It cannot prove the cited bytes support the
statement. An answer that cites the right function and describes it wrongly
passes every check here. That gap is named in ARCHITECTURE_PIVOT_AFTER_J1.md §8
and is a J-2 measurement, not something this file quietly implies it has solved.

Structural checks follow the epistemic rule (§11, Correction 2):

    SUPPORTED               a deterministic claim affirms the assertion
    EXPLICIT_CONTRADICTION  a deterministic claim denies it, AND the predicate
                            is closed-world complete for that scope
    NOT_ESTABLISHED         the graph neither affirms nor denies -- the default

Absence is NOT_ESTABLISHED. It is never a contradiction unless the predicate
carries an explicit completeness guarantee.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from kgc.predicates import completeness_scope, may_falsify_by_absence
from kgq.contract import normalise, sentences

SUPPORTED = "SUPPORTED"
EXPLICIT_CONTRADICTION = "EXPLICIT_CONTRADICTION"
NOT_ESTABLISHED = "NOT_ESTABLISHED"

ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"


@dataclass
class EvidenceCheck:
    evidence_id: str
    exists: bool = False
    retrieved: bool = False
    locator_valid: bool = False
    bytes_match: bool = False
    quote_found: bool | None = None
    rel_path: str | None = None
    byte_start: int | None = None
    byte_end: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (self.exists and self.retrieved and self.locator_valid
                and self.bytes_match and self.quote_found is not False)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()} | {"ok": self.ok}


@dataclass
class StructuralCheck:
    predicate: str
    subject: str
    object: str
    status: str
    detail: str
    claim_id: str | None = None
    completeness: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class ClaimVerdict:
    text: str
    evidence: list[EvidenceCheck] = field(default_factory=list)
    structural: list[StructuralCheck] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {"text": self.text, "ok": self.ok, "problems": self.problems,
                "evidence": [e.as_dict() for e in self.evidence],
                "structural": [s.as_dict() for s in self.structural]}


@dataclass
class Verdict:
    outcome: str                       # ACCEPTED | REJECTED
    claims: list[ClaimVerdict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def contradictions(self) -> list[StructuralCheck]:
        return [s for c in self.claims for s in c.structural
                if s.status == EXPLICIT_CONTRADICTION]

    def reason(self) -> str:
        if self.outcome == ACCEPTED:
            return "every material statement cites evidence that exists, was retrieved " \
                   "for this question, and still matches the source bytes"
        lines = list(self.problems)
        for c in self.claims:
            for p in c.problems:
                lines.append(f"statement {c.text[:60]!r}: {p}")
        return "; ".join(lines) or "rejected"

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "problems": self.problems,
                "reason": self.reason(),
                "claims": [c.as_dict() for c in self.claims]}


class Validator:
    def __init__(self, con, corpus_root: str):
        self.con = con
        self.root = Path(corpus_root)

    # ── evidence ────────────────────────────────────────────────────────
    def check_evidence(self, evidence_id: str, retrieved_ids: set[str]) -> EvidenceCheck:
        c = EvidenceCheck(evidence_id)
        row = self.con.execute(
            "SELECT e.locator, e.quoted_text, a.rel_path"
            "  FROM evidence e JOIN artifact a ON a.artifact_id = e.artifact_id"
            " WHERE e.evidence_id = ?", (evidence_id,)).fetchone()
        if row is None:
            c.error = "no such evidence id in the store (fabricated citation)"
            return c
        c.exists = True
        c.retrieved = evidence_id in retrieved_ids
        if not c.retrieved:
            c.error = "evidence exists but was not retrieved for this question"
            return c
        loc = json.loads(row["locator"])
        c.rel_path, c.byte_start, c.byte_end = row["rel_path"], loc.get("byte_start"), loc.get("byte_end")
        path = self.root / row["rel_path"]
        if not path.is_file() or c.byte_start is None:
            c.error = "locator does not address a file in this corpus"
            return c
        data = path.read_bytes()
        if not (0 <= c.byte_start <= c.byte_end <= len(data)):
            c.error = f"byte range [{c.byte_start},{c.byte_end}] outside a {len(data)}-byte file"
            return c
        c.locator_valid = True
        try:
            actual = data[c.byte_start:c.byte_end].decode("utf-8")
        except UnicodeDecodeError as e:
            c.error = f"cited bytes are not UTF-8: {e}"
            return c
        c.bytes_match = actual == row["quoted_text"]
        if not c.bytes_match:
            c.error = "stored quotation no longer matches the source at that range"
        return c

    # ── structural assertions ───────────────────────────────────────────
    def check_structural(self, dep: dict) -> StructuralCheck:
        pred, subj, obj = dep["predicate"], dep["subject"], dep["object"]
        closed = may_falsify_by_absence(pred)
        rows = [dict(r) for r in self.con.execute(
            "SELECT cl.claim_id, cl.establishment, cl.object_literal,"
            "       o.qualified_name AS obj, a.parse_status, s.qualified_name AS subj"
            "  FROM claim cl JOIN symbol s ON s.symbol_id = cl.subject_id"
            "  JOIN artifact a ON a.artifact_id = s.artifact_id"
            "  LEFT JOIN symbol o ON o.symbol_id = cl.object_id"
            " WHERE cl.predicate = ? AND (s.qualified_name = ? OR s.name = ?)",
            (pred, subj, subj.split(".")[-1]))]

        def names(r):
            full = r["obj"] or r["object_literal"] or ""
            return {full, full.split(".")[-1]}

        want = {obj, obj.split(".")[-1]}
        for r in rows:
            if want & names(r):
                return StructuralCheck(pred, subj, obj, SUPPORTED,
                                       f"{r['establishment']} claim in the compiled graph",
                                       r["claim_id"], "CLOSED_WORLD" if closed else "OPEN_WORLD")
        if not rows:
            return StructuralCheck(pred, subj, obj, NOT_ESTABLISHED,
                                   f"no {pred} claim exists for {subj!r}; the graph says "
                                   f"nothing either way",
                                   completeness="CLOSED_WORLD" if closed else "OPEN_WORLD")
        if not closed:
            return StructuralCheck(
                pred, subj, obj, NOT_ESTABLISHED,
                f"{pred} is OPEN_WORLD -- absence proves nothing (unresolved targets, "
                f"dynamic dispatch and excluded call sites all produce absences)",
                completeness="OPEN_WORLD")
        # closed-world: absence is meaningful only if the artifact parsed cleanly
        if any(r["parse_status"] != "OK" for r in rows):
            return StructuralCheck(pred, subj, obj, NOT_ESTABLISHED,
                                   "the defining artifact did not parse cleanly, so the "
                                   "completeness guarantee does not hold",
                                   completeness="CLOSED_WORLD")
        have = sorted({(r["obj"] or r["object_literal"] or "") for r in rows})
        return StructuralCheck(
            pred, subj, obj, EXPLICIT_CONTRADICTION,
            f"{pred} is closed-world for {completeness_scope(pred)[:70]}...; the graph "
            f"records exactly {have} for {subj!r}, and {obj!r} is not among them",
            completeness="CLOSED_WORLD")

    # ── the gate ────────────────────────────────────────────────────────
    def check_answer_coverage(self, model_answer) -> list[str]:
        """Every sentence of `answer` must also be a claim.

        Without this, a model can attach evidence to one statement and smuggle a
        second, unsupported one into the prose beside it -- reproduced:
        `answer` said "...It also encrypts every file on disk." while `claims`
        carried only the supported sentence, and the gate said ACCEPTED.

        The check is string identity after normalisation. It is NOT an entailment
        test and does not pretend to be one: it only establishes that nothing
        appears in the answer that was not put forward as a claim.
        """
        claim_norms = [normalise(c.text) for c in model_answer.claims]
        problems = []
        for s in sentences(model_answer.answer):
            n = normalise(s)
            if not n:
                continue
            if not any(n == cn or n in cn or cn in n for cn in claim_norms if cn):
                problems.append(
                    f"the answer contains a statement that is not among the claims and "
                    f"therefore has no evidence: {s[:90]!r}")
        return problems

    def validate(self, model_answer, retrieved_ids: set[str]) -> Verdict:
        v = Verdict(ACCEPTED)
        if not model_answer.claims:
            # A no-claims reply is an honest "cannot establish", not an answer.
            v.outcome = REJECTED
            v.problems.append("the reply made no evidence-backed statement")
            return v
        for mc in model_answer.claims:
            cv = ClaimVerdict(mc.text)
            if not mc.evidence_ids:
                cv.problems.append("material statement with no evidence mapping")
            for eid in mc.evidence_ids:
                ec = self.check_evidence(eid, retrieved_ids)
                if ec.ok and mc.quote:
                    row = self.con.execute("SELECT quoted_text FROM evidence WHERE evidence_id=?",
                                           (eid,)).fetchone()
                    ec.quote_found = mc.quote.strip() in (row["quoted_text"] if row else "")
                cv.evidence.append(ec)
            if mc.evidence_ids and not any(e.ok for e in cv.evidence):
                cv.problems.append("; ".join(
                    f"evidence {e.evidence_id[:10]}: {e.error or 'quotation not found in cited bytes'}"
                    for e in cv.evidence))
            for dep in mc.structural_dependencies:
                sc = self.check_structural(dep)
                cv.structural.append(sc)
                if sc.status == EXPLICIT_CONTRADICTION:
                    cv.problems.append(
                        f"structural assertion {sc.predicate}({sc.subject}, {sc.object}) is "
                        f"contradicted by the compiled graph: {sc.detail}")
            v.claims.append(cv)
        v.problems += self.check_answer_coverage(model_answer)
        if v.problems or any(not c.ok for c in v.claims):
            v.outcome = REJECTED
        return v
