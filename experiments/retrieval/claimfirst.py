"""Claim-first retrieval and support (designs S-C / S-D).

The support layer NEVER sees the retrieval score, rank or lexical overlap. It
sees typed constraints, compiled claims and verified evidence. That separation is
the whole point: Gate 1.75 failed because rank and overlap leaked into support.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from experiments.retrieval.constraints import Constraints, extract

ABSTAIN, EXPOSE, EXPOSE_CONFLICTED = "ABSTAIN", "EXPOSE", "EXPOSE_CONFLICTED"


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


class ClaimIndex:
    def __init__(self, store):
        self.store = store

    # ---------- entity resolution ----------
    def find_symbols(self, name: str) -> list[dict]:
        bare = name.split(".")[-1].rstrip("(),.?")
        rows = self.store.con.execute(
            "SELECT s.symbol_id, s.qualified_name, s.name, s.kind, a.rel_path"
            "  FROM symbol s JOIN artifact a USING(artifact_id)"
            " WHERE s.name = ? OR s.qualified_name = ? OR s.qualified_name LIKE ?"
            " ORDER BY length(s.qualified_name)",
            (bare, name, f"%.{name}")).fetchall()
        return [dict(r) for r in rows]

    def defining_claims(self, symbol_id: str) -> list[ClaimHit]:
        """Claims where this symbol is the OBJECT.

        A constant or class attribute has no outgoing claims; its definition
        evidence is carried by the incoming CONTAINS claim. Looking only at
        outgoing claims made every leaf symbol appear to have no compiled claim.
        """
        rows = self.store.con.execute(
            "SELECT cl.claim_id, cl.predicate, cl.object_literal, cl.establishment,"
            "       s1.qualified_name AS subj, s2.qualified_name AS obj,"
            "       a.rel_path, e.quoted_text, e.verification_strength"
            "  FROM claim cl"
            "  JOIN symbol s1 ON s1.symbol_id = cl.subject_id"
            "  JOIN symbol s2 ON s2.symbol_id = cl.object_id"
            "  JOIN claim_evidence ce ON ce.claim_id = cl.claim_id"
            "  JOIN evidence e ON e.evidence_id = ce.evidence_id"
            "  JOIN artifact a ON a.artifact_id = e.artifact_id"
            " WHERE cl.object_id = ? AND cl.predicate = 'CONTAINS'", (symbol_id,)).fetchall()
        return [ClaimHit(r["claim_id"], r["predicate"], r["subj"], r["obj"],
                         r["object_literal"], r["rel_path"], r["quoted_text"],
                         r["verification_strength"], r["establishment"]) for r in rows]

    def claims_of(self, symbol_id: str, predicate: str | None = None) -> list[ClaimHit]:
        q = ("SELECT cl.claim_id, cl.predicate, cl.object_literal, cl.establishment,"
             "       s1.qualified_name AS subj, s2.qualified_name AS obj,"
             "       a.rel_path, e.quoted_text, e.verification_strength"
             "  FROM claim cl"
             "  JOIN symbol s1 ON s1.symbol_id = cl.subject_id"
             "  LEFT JOIN symbol s2 ON s2.symbol_id = cl.object_id"
             "  JOIN claim_evidence ce ON ce.claim_id = cl.claim_id"
             "  JOIN evidence e ON e.evidence_id = ce.evidence_id"
             "  JOIN artifact a ON a.artifact_id = e.artifact_id"
             " WHERE cl.subject_id = ?")
        args = [symbol_id]
        if predicate:
            q += " AND cl.predicate = ?"; args.append(predicate)
        return [ClaimHit(r["claim_id"], r["predicate"], r["subj"], r["obj"],
                         r["object_literal"], r["rel_path"], r["quoted_text"],
                         r["verification_strength"], r["establishment"])
                for r in self.store.con.execute(q, args)]

    def children_of(self, qualified_name: str) -> list[dict]:
        return [dict(r) for r in self.store.con.execute(
            "SELECT s.symbol_id, s.qualified_name, s.name, s.kind, a.rel_path"
            "  FROM symbol s JOIN artifact a USING(artifact_id)"
            " WHERE s.qualified_name LIKE ?", (f"{qualified_name}.%",))]


def retrieve_claims(index: ClaimIndex, c: Constraints, k: int = 10) -> list[ClaimHit]:
    """Candidate claims for the constraints. Ranking here is structural, not lexical."""
    if not c.parsed or not c.entities:
        return []
    out: list[ClaimHit] = []
    for ent in c.entities:
        for sym in index.find_symbols(ent)[:4]:
            if c.relation:
                out += index.claims_of(sym["symbol_id"], c.relation)
            elif c.prop:
                # A named property must resolve to a child symbol. Falling back to
                # the entity's own claims answers "what is the default salt of
                # BadSignature" with BadSignature's definition -- the
                # correct-entity/wrong-property failure. No fallback.
                prop = c.prop.lower()
                _ = index.defining_claims  # property path never falls back
                parts = [w for w in prop.split("_") if len(w) > 2]
                for ch in index.children_of(sym["qualified_name"]):
                    nm = ch["name"].lower()
                    if nm == prop or prop in nm or (parts and all(w in nm for w in parts)):
                        out += index.claims_of(ch["symbol_id"])
            else:
                out += index.claims_of(sym["symbol_id"])
                out += index.defining_claims(sym["symbol_id"])
    seen, uniq = set(), []
    for h in out:
        if h.claim_id not in seen:
            seen.add(h.claim_id); uniq.append(h)
    return uniq[:k]


def decide(index: ClaimIndex, query: str, k: int = 10):
    """Deterministic support decision over compiled claims."""
    c = extract(query)
    if not c.parsed:
        return ABSTAIN, "query does not parse into typed constraints", [], c

    resolved = [s for e in c.entities for s in index.find_symbols(e)]
    if not resolved:
        return ABSTAIN, f"entity {c.entities} is not a compiled symbol", [], c

    hits = retrieve_claims(index, c, k)
    if not hits:
        what = (f"relation {c.relation}" if c.relation
                else f"property {c.prop!r}" if c.prop else "claims")
        return ABSTAIN, f"no compiled claim provides {what} for {c.entities}", [], c

    # relation object must actually match a compiled edge
    if c.relation and c.relation_object:
        want = c.relation_object.split(".")[-1].rstrip("(),.?").lower()
        hits = [h for h in hits
                if (h.object_qname or h.object_literal or "").lower().split(".")[-1] == want]
        if not hits:
            return ABSTAIN, (f"no {c.relation} edge from {c.entities[0]} to "
                             f"{c.relation_object}"), [], c

    # scope constraint: the claim's evidence must come from the named file
    if c.source_scope:
        scoped = [h for h in hits if h.rel_path.endswith(c.source_scope)]
        if not scoped:
            return ABSTAIN, (f"claim exists but its evidence is in "
                             f"{sorted({h.rel_path for h in hits})}, not {c.source_scope}"), [], c
        hits = scoped

    # literal constraint: a stated literal must appear in the claim's evidence
    if c.literals:
        keep = [h for h in hits if all(
            re.search(rf"\b{re.escape(l)}\b", h.evidence_text) for l in c.literals)]
        if not keep:
            return ABSTAIN, f"literals {c.literals} do not appear in the claim's evidence", [], c
        hits = keep

    verifiable = [h for h in hits if h.evidence_strength in ("EXACT", "REPRODUCIBLE")]
    if not verifiable:
        return ABSTAIN, "no candidate claim carries verifiable evidence", [], c

    values = {h.evidence_text.strip() for h in verifiable
              if h.predicate == verifiable[0].predicate}
    outcome = EXPOSE_CONFLICTED if len(values) > 1 and c.prop else EXPOSE
    return outcome, "compiled claim with verified evidence", verifiable, c
