"""Claim-first support. The trust boundary, enforced (CLAIM_TRUST_BOUNDARY.md).

The decision depends ONLY on: interpreted query constraints, the compiled claim,
its provenance, its establishment and lifecycle, its verified evidence, and
scope/conflict information.

It never sees retrieval score, rank, candidate ordering, lexical overlap or model
confidence. The retriever is not consulted here at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from experiments.retrieval.constraints import ParseStatus, QueryConstraints, extract
from kgc.claim_value import DIFFERENT, SAME, UNRESOLVED, compare
from kgc.artifact_identity import canonical_path, resolve_scope
from kgc.predicates import is_trusted, may_contradict

ABSTAIN = "ABSTAIN"
ABSTAIN_AMBIGUOUS = "ABSTAIN_AMBIGUOUS"
EXPOSE = "EXPOSE"
EXPOSE_CONFLICTED = "EXPOSE_CONFLICTED"

# This module holds NO establishment policy of its own. `is_trusted` in
# kgc/predicates.py is the single authority, so editing a predicate's definition
# changes the trust decision here with no second set to keep in step.
PRESENTABLE_LIFECYCLE = frozenset({"ACTIVE", "VERIFIED"})
VERIFIABLE_STRENGTH = frozenset({"EXACT", "REPRODUCIBLE"})


@dataclass
class ClaimHit:
    claim_id: str
    predicate: str
    subject_qname: str
    object_qname: str | None
    object_literal: str | None
    rel_path: str
    evidence_text: str
    evidence_strength: str
    establishment: str
    lifecycle: str

    def value(self) -> str | None:
        return self.object_literal or self.object_qname or self.evidence_text


@dataclass
class Decision:
    outcome: str
    reason: str
    failed_invariant: str | None = None
    hits: list = field(default_factory=list)
    constraints: QueryConstraints | None = None
    conflict: dict | None = None


_CLAIM_SELECT = (
    "SELECT cl.claim_id, cl.predicate, cl.object_literal, cl.establishment, cl.lifecycle,"
    "       s1.qualified_name AS subj, s2.qualified_name AS obj,"
    "       a.rel_path, e.quoted_text, e.verification_strength"
    "  FROM claim cl"
    "  JOIN symbol s1 ON s1.symbol_id = cl.subject_id"
    "  LEFT JOIN symbol s2 ON s2.symbol_id = cl.object_id"
    "  JOIN claim_evidence ce ON ce.claim_id = cl.claim_id"
    "  JOIN evidence e ON e.evidence_id = ce.evidence_id"
    "  JOIN artifact a ON a.artifact_id = e.artifact_id")


def _hit(r) -> ClaimHit:
    return ClaimHit(r["claim_id"], r["predicate"], r["subj"], r["obj"], r["object_literal"],
                    r["rel_path"], r["quoted_text"], r["verification_strength"],
                    r["establishment"], r["lifecycle"])


class ClaimIndex:
    def __init__(self, store):
        self.store = store

    def artifact_paths(self) -> list[str]:
        return [r["rel_path"] for r in self.store.con.execute(
            "SELECT DISTINCT rel_path FROM artifact")]

    def find_symbols(self, name: str, scope: str | None = None) -> list[dict]:
        """ALL symbols matching the name. Never truncated: truncation would let
        candidate ordering decide semantic identity.

        `scope`, when given, must be a CANONICAL artifact path and is matched
        exactly -- not by suffix.
        """
        bare = name.split(".")[-1].rstrip("(),.?")
        rows = self.store.con.execute(
            "SELECT s.symbol_id, s.qualified_name, s.name, s.kind, s.parent_id, a.rel_path"
            "  FROM symbol s JOIN artifact a USING(artifact_id)"
            # GLOB, not LIKE: SQLite's LIKE is case-insensitive for ASCII, which
            # conflated the class `Signer` with the module `signer` and with a
            # local variable named `signer` -- three different entities -- and
            # produced a spurious ambiguity abstention.
            " WHERE s.name = ? OR s.qualified_name = ? OR s.qualified_name GLOB ?"
            " ORDER BY s.qualified_name",
            (bare, name, f"*.{name}")).fetchall()
        out = [dict(r) for r in rows]
        if scope:
            want = canonical_path(scope)
            return [r for r in out if canonical_path(r["rel_path"]) == want]
        return out

    def direct_children(self, symbol_id: str) -> list[dict]:
        """DIRECT children only, by parent identity.

        A LIKE 'entity.%' match returns descendants, which would let
        `Signer.config.default.timeout` answer a question about `Signer.timeout`.
        """
        return [dict(r) for r in self.store.con.execute(
            "SELECT s.symbol_id, s.qualified_name, s.name, s.kind, a.rel_path"
            "  FROM symbol s JOIN artifact a USING(artifact_id)"
            " WHERE s.parent_id = ? ORDER BY s.qualified_name", (symbol_id,))]

    def claims_from(self, symbol_id: str, predicate: str | None = None) -> list[ClaimHit]:
        q, args = _CLAIM_SELECT + " WHERE cl.subject_id = ?", [symbol_id]
        if predicate:
            q += " AND cl.predicate = ?"; args.append(predicate)
        return [_hit(r) for r in self.store.con.execute(q, args)]

    def defining_claims(self, symbol_id: str) -> list[ClaimHit]:
        """Claims where the symbol is the OBJECT: a leaf constant's definition
        evidence lives on the incoming CONTAINS claim."""
        return [_hit(r) for r in self.store.con.execute(
            _CLAIM_SELECT + " WHERE cl.object_id = ? AND cl.predicate = 'CONTAINS'",
            (symbol_id,))]


def _match_property(index: ClaimIndex, sym: dict, prop_word: str) -> list[ClaimHit]:
    """Resolve a property word against DIRECT children only."""
    prop = prop_word.lower()
    parts = [w for w in prop.split("_") if len(w) > 2]
    out: list[ClaimHit] = []
    for ch in index.direct_children(sym["symbol_id"]):
        nm = ch["name"].lower()
        if nm == prop or (parts and all(w in nm for w in parts)) or prop in nm:
            out += index.claims_from(ch["symbol_id"])
            out += index.defining_claims(ch["symbol_id"])
    return out


def decide(index: ClaimIndex, query: str) -> Decision:
    c = extract(query)

    # (1) constraints must fully identify the question
    if c.parse_status is ParseStatus.UNPARSEABLE:
        return Decision(ABSTAIN, "query does not parse into typed constraints",
                        "INV-1_constraints", constraints=c)
    if c.parse_status is ParseStatus.PARTIAL:
        return Decision(ABSTAIN,
                        f"query only partially parses ({c.shape}); refusing to answer "
                        f"a different question", "INV-1_constraints", constraints=c)
    if c.parse_status is ParseStatus.AMBIGUOUS:
        return Decision(ABSTAIN_AMBIGUOUS,
                        f"query names several entities {c.subjects} with no disambiguator",
                        "INV-10_ambiguity", constraints=c)

    # (1b) scope resolution — one canonical identity, or abstain
    scope_path = None
    if c.source_scope:
        matches, status = resolve_scope(c.source_scope, index.artifact_paths())
        if status == "AMBIGUOUS":
            return Decision(ABSTAIN_AMBIGUOUS,
                            f"scope {c.source_scope!r} matches {len(matches)} artifacts "
                            f"({matches[:3]}); a basename is not an identity",
                            "INV-10_ambiguity", constraints=c)
        if status == "UNKNOWN":
            return Decision(ABSTAIN, f"scope {c.source_scope!r} names no artifact in the corpus",
                            "INV-4_scope", constraints=c)
        scope_path = matches[0]

    # (2) subject resolution — ambiguity abstains, never picks
    syms = index.find_symbols(c.subject, scope_path)
    if not syms:
        return Decision(ABSTAIN, f"subject {c.subject!r} is not a compiled symbol",
                        "INV-1_subject", constraints=c)
    distinct = {s["qualified_name"] for s in syms}
    if len(distinct) > 1:
        return Decision(ABSTAIN_AMBIGUOUS,
                        f"subject {c.subject!r} resolves to {len(distinct)} symbols "
                        f"({sorted(distinct)[:3]}...) and no scope disambiguates",
                        "INV-10_ambiguity", constraints=c)

    # (3) gather claims for the requested predicate/property
    hits: list[ClaimHit] = []
    for s in syms:
        if c.predicate and c.shape == "relation":
            hits += index.claims_from(s["symbol_id"], c.predicate)
        elif c.prop_word:
            hits += _match_property(index, s, c.prop_word)
        else:
            hits += index.claims_from(s["symbol_id"])
            hits += index.defining_claims(s["symbol_id"])
    seen, uniq = set(), []
    for h in hits:
        if h.claim_id not in seen:
            seen.add(h.claim_id); uniq.append(h)
    hits = uniq
    if not hits:
        what = (f"predicate {c.predicate}" if c.predicate
                else f"property {c.prop_word!r}" if c.prop_word else "claims")
        return Decision(ABSTAIN, f"no compiled claim provides {what} for {c.subject!r}",
                        "INV-2_predicate", constraints=c)

    # (4) object constraint
    if c.obj:
        want = c.obj.split(".")[-1].rstrip("(),.?").lower()
        hits = [h for h in hits
                if (h.object_qname or h.object_literal or "").lower().split(".")[-1] == want]
        if not hits:
            return Decision(ABSTAIN, f"no {c.predicate} claim from {c.subject!r} to {c.obj!r}",
                            "INV-3_object", constraints=c)

    # (5) scope constraint — applied to EVIDENCE identity, exactly
    if scope_path:
        scoped = [h for h in hits if canonical_path(h.rel_path) == scope_path]
        if not scoped:
            return Decision(ABSTAIN,
                            f"claim exists but its evidence is in "
                            f"{sorted({h.rel_path for h in hits})[:2]}, not {scope_path}",
                            "INV-4_scope", constraints=c)
        hits = scoped

    # (6) ESTABLISHMENT — the boundary Gate 2 described but did not enforce
    trusted = [h for h in hits if is_trusted(h.predicate, h.establishment)]
    untrusted = [h for h in hits if not is_trusted(h.predicate, h.establishment)]
    if not trusted:
        levels = sorted({h.establishment for h in untrusted})
        return Decision(ABSTAIN,
                        f"only {levels} claims exist; a model-proposed claim cannot "
                        f"become a trusted answer",
                        "INV-5_establishment", hits=untrusted, constraints=c)

    # (7) lifecycle
    presentable = [h for h in trusted if h.lifecycle in PRESENTABLE_LIFECYCLE]
    if not presentable:
        return Decision(ABSTAIN,
                        f"claims exist but none has a presentable lifecycle "
                        f"({sorted({h.lifecycle for h in trusted})})",
                        "INV-6_lifecycle", hits=trusted, constraints=c)

    # (8-9) evidence exists, is verifiable, and belongs to the claim (SQL join)
    verified = [h for h in presentable if h.evidence_strength in VERIFIABLE_STRENGTH]
    if not verified:
        return Decision(ABSTAIN, "no candidate claim carries EXACT/REPRODUCIBLE evidence",
                        "INV-8_evidence", hits=presentable, constraints=c)

    # (10) literal constraint
    if c.literal:
        keep = [h for h in verified
                if all(re.search(rf"\b{re.escape(l)}\b", h.evidence_text) for l in c.literal)]
        if not keep:
            return Decision(ABSTAIN,
                            f"literals {c.literal} do not appear in the claim's evidence",
                            "INV-3_object", hits=verified, constraints=c)
        verified = keep

    # (11) conflict on CLAIM SEMANTICS, not evidence wording
    conflict = _conflict(verified)
    if conflict["state"] == "CONTRADICTS":
        return Decision(EXPOSE_CONFLICTED,
                        f"supported, but sources assert incompatible values "
                        f"{conflict['values']}", hits=verified, constraints=c,
                        conflict=conflict)
    return Decision(EXPOSE, "compiled claim, trusted establishment, verified evidence",
                    hits=verified, constraints=c, conflict=conflict)


def _conflict(hits: list[ClaimHit]) -> dict:
    """Compare normalized claim values, but only where the PREDICATE SPEC says a
    differing value can mean contradiction.

    Cardinality lives in kgc/predicates.py, not here: `f()` calling both `a()`
    and `b()` is two facts. Grouping by predicate alone made every multi-valued
    relationship self-contradictory.
    """
    by_pred: dict[str, list[ClaimHit]] = {}
    for h in hits:
        by_pred.setdefault(h.predicate, []).append(h)
    for pred, group in by_pred.items():
        if not may_contradict(pred):
            continue                       # MULTI_VALUED: more objects is more facts
        vals = [h.value() for h in group]
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                if compare(vals[i], vals[j]) == DIFFERENT:
                    return {"state": "CONTRADICTS", "predicate": pred,
                            "values": [str(vals[i])[:40], str(vals[j])[:40]],
                            "sources": [group[i].rel_path, group[j].rel_path]}
    return {"state": "CONSISTENT", "values": []}
