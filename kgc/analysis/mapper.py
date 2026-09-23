"""CodeAnalysis -> canonical IR.

The seam that keeps a backend's worldview out of the IR. Every structural fact
becomes a Claim with byte-exact evidence; resolution quality is recorded
alongside rather than being baked into the predicate.
"""
from __future__ import annotations

from kgc import SCHEMA_VERSION
from kgc.analysis.interface import CodeAnalysis
from kgc.evidence import make_evidence, verify
from kgc.ids import claim_id, symbol_id
from kgc.ir import (Claim, Establishment, Evidence, Lifecycle, Resolution, Symbol)
from kgc.predicates import LITERAL_OBJECT


def map_analysis(an: CodeAnalysis, *, artifact_id: str, data: bytes, run_id: str,
                 resolved_refs=None, extractor_version: str | None = None,
                 global_symbols: dict | None = None):
    """Return (symbols, claims_with_evidence, reference_details)."""
    symbols: list[Symbol] = []
    by_qname: dict[str, str] = {}

    for rs in an.symbols:
        sid = symbol_id(artifact_id, rs.qualified_name, rs.kind,
                        rs.locator.payload["byte_start"], rs.locator.payload["byte_end"])
        by_qname[rs.qualified_name] = sid
        symbols.append(Symbol(sid, artifact_id, None, rs.kind, rs.name,
                              rs.qualified_name, rs.locator, rs.docstring))

    # resolve parent links now that every qname has an id
    symbols = [Symbol(s.symbol_id, s.artifact_id,
                      by_qname.get(rs.parent_qname) if rs.parent_qname else None,
                      s.kind, s.name, s.qualified_name, s.locator, s.docstring)
               for s, rs in zip(symbols, an.symbols)]

    refs_in = an.references if resolved_refs is None else resolved_refs
    ver = extractor_version or an.backend_version
    claims: list[tuple[Claim, list[Evidence]]] = []
    refs: list[tuple[str, str, str, str]] = []

    gsym = global_symbols or {}

    def emit(predicate, subject_qn, object_qn, object_literal, locator, quoted):
        subj = by_qname.get(subject_qn)
        if subj is None:
            return None
        ev = verify(make_evidence(artifact_id, data, locator, quoted), data)
        # look in this artifact first, then corpus-wide: a resolved cross-module
        # target lives in another artifact
        obj = (by_qname.get(object_qn) or gsym.get(object_qn)) if object_qn else None
        cid = claim_id(predicate=predicate, subject_id=subj, object_id=obj,
                       object_literal=object_literal,
                       extractor_id=an.backend_id, extractor_version=ver,
                       model_id=None, prompt_version=None, schema_version=SCHEMA_VERSION,
                       evidence_ids=[ev.evidence_id])
        claims.append((Claim(
            claim_id=cid, predicate=predicate, subject_id=subj, subject_kind="symbol",
            object_id=obj, object_kind="symbol" if obj else None,
            object_literal=object_literal,
            lifecycle=Lifecycle.ACTIVE, establishment=Establishment.DERIVED,
            confidence=None,                      # a parser result is not a probability
            extractor_id=an.backend_id, extractor_version=ver,
            model_id=None, prompt_version=None, schema_version=SCHEMA_VERSION,
            run_id=run_id, evidence_ids=[ev.evidence_id]), [ev]))
        return cid

    # containment: parent CONTAINS child
    for s, rs in zip(symbols, an.symbols):
        if rs.parent_qname and rs.parent_qname in by_qname:
            quoted = data[s.locator.payload["byte_start"]:s.locator.payload["byte_end"]].decode("utf-8")
            emit("CONTAINS", rs.parent_qname, rs.qualified_name, None, s.locator, quoted)

    # references: object_id is set ONLY when resolution is DETERMINISTIC
    for rr in refs_in:
        subject_qn = rr.from_qname or an.symbols[0].qualified_name
        quoted = data[rr.locator.payload["byte_start"]:rr.locator.payload["byte_end"]].decode("utf-8")
        object_qn = rr.to_qname if rr.resolution in (
            Resolution.DETERMINISTIC, Resolution.HEURISTIC) else None
        cid = emit(rr.predicate, subject_qn, object_qn,
                   None if object_qn else rr.to_name, rr.locator, quoted)
        # The `reference` table records how well a target SYMBOL was resolved. A
        # literal-valued claim has no target symbol, so it gets no row: writing
        # one would assert DETERMINISTIC resolution with a null object_id, which
        # check_invariants correctly reports as an unsupported assertion.
        if cid and rr.predicate not in LITERAL_OBJECT:
            refs.append((cid, rr.to_name, rr.resolution.value, rr.reason))
    return symbols, claims, refs
