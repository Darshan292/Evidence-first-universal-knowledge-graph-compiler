"""CodeAnalysis -> canonical IR.

The seam that keeps a backend's worldview out of the IR. Every structural fact
becomes a Claim with byte-exact evidence; resolution quality is recorded
alongside rather than being baked into the predicate.

IDENTITY RULE (K-1.1). A qualified name is a NAME, not an identity. The same
qualified name can occur many times in one artifact:

    def outer():
        if cond:
            def worker(): value = 1     # outer.worker, outer.worker.value
        else:
            def worker(): value = 2     # outer.worker, outer.worker.value

`by_qname[qualified_name] = symbol_id` kept whichever occurrence came last, so a
child of the FIRST `worker` was given the SECOND `worker`'s id. Measured on
werkzeug: 18 children and 50 reference subjects attached to the wrong
occurrence, and 7 artifacts lost entirely when the resulting forward parent
reference violated the symbol foreign key.

Identity is therefore resolved by SOURCE OCCURRENCE: the parent of a symbol, and
the subject of a reference, is the occurrence of that qualified name whose byte
span CONTAINS it. `symbol_id` already includes the byte span, so no new
identifier scheme is needed. Where no unique occurrence can be established
nothing is attached -- an unresolved edge is better than a wrong one.
"""
from __future__ import annotations

from kgc import SCHEMA_VERSION
from kgc.analysis.interface import CodeAnalysis
from kgc.evidence import make_evidence, verify
from kgc.ids import claim_id, symbol_id
from kgc.ir import (Claim, Diagnostic, Establishment, Evidence, Lifecycle,
                    Resolution, Symbol)
from kgc.predicates import LITERAL_OBJECT


class OccurrenceIndex:
    """Qualified name -> every source occurrence of it, with its symbol id.

    Deliberately not a general resolver: two lookups, both answering "which
    concrete occurrence", and nothing else.
    """

    def __init__(self):
        self._by_qname: dict[str, list[tuple[int, int, str]]] = {}

    def add(self, qname: str, byte_start: int, byte_end: int, sid: str) -> None:
        self._by_qname.setdefault(qname, []).append((byte_start, byte_end, sid))

    def occurrences(self, qname: str) -> list[tuple[int, int, str]]:
        return self._by_qname.get(qname, [])

    def enclosing(self, qname: str | None, byte_start: int, byte_end: int) -> str | None:
        """The occurrence of `qname` whose span contains [byte_start, byte_end].

        Containment is inclusive: a claim about an assignment cites exactly that
        assignment's span. Returns None when no occurrence contains the span, or
        when the tightest one is not unique -- never a guess.
        """
        if qname is None:
            return None
        inside = [o for o in self._by_qname.get(qname, ())
                  if o[0] <= byte_start and byte_end <= o[1]]
        if len(inside) == 1:
            return inside[0][2]
        if not inside:
            return None
        width = min(o[1] - o[0] for o in inside)
        tightest = [o for o in inside if o[1] - o[0] == width]
        return tightest[0][2] if len(tightest) == 1 else None

    def owner(self, qname: str | None, byte_start: int, byte_end: int) -> str | None:
        """Which occurrence of `qname` a reference site at [start, end] belongs to.

        A parent link asserts lexical containment, so `enclosing` is definitional
        there. A reference subject is a different assertion -- "this call site
        belongs to symbol S" -- where containment is only the DISAMBIGUATOR
        among same-named candidates. A decorator's call site sits above its
        function's own byte span (`FunctionDef.lineno` points at `def`), so
        demanding containment here would drop 283 real edges on werkzeug.

        So: the enclosing occurrence when one exists, otherwise the only
        occurrence when the name has just one, otherwise nothing. The one case
        that is refused is the one that would be a guess: several occurrences and
        no containment.
        """
        if qname is None:
            return None
        hit = self.enclosing(qname, byte_start, byte_end)
        if hit is not None:
            return hit
        occ = self._by_qname.get(qname, ())
        return occ[0][2] if len(occ) == 1 else None

    def defines(self, qname: str | None) -> int:
        """How many times THIS artifact defines `qname`."""
        return len(self._by_qname.get(qname, ())) if qname else 0

    def sole_definition(self, qname: str | None) -> str | None:
        """The symbol id for `qname` when this artifact defines it exactly once.

        A reference TARGET carries no source span of its own, so a duplicated
        name cannot be disambiguated without data flow. Returning None keeps the
        edge unresolved instead of attaching it to an arbitrary occurrence.
        """
        occ = self._by_qname.get(qname) if qname else None
        return occ[0][2] if occ and len(occ) == 1 else None


def map_analysis(an: CodeAnalysis, *, artifact_id: str, data: bytes, run_id: str,
                 resolved_refs=None, extractor_version: str | None = None,
                 global_symbols: dict | None = None):
    """Return (symbols, claims_with_evidence, reference_details, diagnostics).

    `symbols` is ordered parent-before-child, so the caller can insert it
    directly without violating symbol.parent_id. That ordering is a persistence
    detail; it is NOT what makes the parent correct.
    """
    index = OccurrenceIndex()
    diagnostics: list[Diagnostic] = []
    sids: list[str] = []

    for rs in an.symbols:
        p = rs.locator.payload
        sid = symbol_id(artifact_id, rs.qualified_name, rs.kind,
                        p["byte_start"], p["byte_end"])
        sids.append(sid)
        index.add(rs.qualified_name, p["byte_start"], p["byte_end"], sid)

    symbols: list[Symbol] = []
    for rs, sid in zip(an.symbols, sids):
        p = rs.locator.payload
        parent = index.enclosing(rs.parent_qname, p["byte_start"], p["byte_end"])
        if rs.parent_qname and parent is None:
            diagnostics.append(Diagnostic(
                artifact_id, "WARNING", "UNRESOLVED_PARENT_OCCURRENCE",
                f"{rs.qualified_name!r} names parent {rs.parent_qname!r} but no single "
                f"occurrence of it encloses this symbol; left unparented",
                p.get("line_start")))
        symbols.append(Symbol(sid, artifact_id, parent, rs.kind, rs.name,
                              rs.qualified_name, rs.locator, rs.docstring))

    # Persistence order only. A parent's span contains its child's, so a parent
    # always sorts first under (start ascending, end descending).
    symbols.sort(key=lambda s: (s.locator.payload["byte_start"],
                                -s.locator.payload["byte_end"]))

    refs_in = an.references if resolved_refs is None else resolved_refs
    ver = extractor_version or an.backend_version
    claims: list[tuple[Claim, list[Evidence]]] = []
    refs: list[tuple[str, str, str, str]] = []
    gsym = global_symbols or {}
    module_sid = sids[0] if sids else None

    def emit(predicate, subject_sid, object_sid, object_literal, locator, quoted):
        """Subject and object arrive as concrete symbol ids, never as names."""
        if subject_sid is None:
            return None
        ev = verify(make_evidence(artifact_id, data, locator, quoted), data)
        cid = claim_id(predicate=predicate, subject_id=subject_sid, object_id=object_sid,
                       object_literal=object_literal,
                       extractor_id=an.backend_id, extractor_version=ver,
                       model_id=None, prompt_version=None, schema_version=SCHEMA_VERSION,
                       evidence_ids=[ev.evidence_id])
        claims.append((Claim(
            claim_id=cid, predicate=predicate, subject_id=subject_sid, subject_kind="symbol",
            object_id=object_sid, object_kind="symbol" if object_sid else None,
            object_literal=object_literal,
            lifecycle=Lifecycle.ACTIVE, establishment=Establishment.DERIVED,
            confidence=None,                      # a parser result is not a probability
            extractor_id=an.backend_id, extractor_version=ver,
            model_id=None, prompt_version=None, schema_version=SCHEMA_VERSION,
            run_id=run_id, evidence_ids=[ev.evidence_id]), [ev]))
        return cid

    # containment: the ENCLOSING occurrence CONTAINS this exact child occurrence
    for rs, sid in zip(an.symbols, sids):
        if not rs.parent_qname:
            continue
        p = rs.locator.payload
        parent_sid = index.enclosing(rs.parent_qname, p["byte_start"], p["byte_end"])
        if parent_sid is None:
            continue                      # already diagnosed above
        quoted = data[p["byte_start"]:p["byte_end"]].decode("utf-8")
        emit("CONTAINS", parent_sid, sid, None, rs.locator, quoted)

    # references: the subject is the occurrence that encloses the reference site
    for rr in refs_in:
        rl = rr.locator.payload
        subject_sid = (index.owner(rr.from_qname, rl["byte_start"], rl["byte_end"])
                       if rr.from_qname else module_sid)
        if subject_sid is None:
            # A wrong CALLS/EXTENDS/IMPORTS subject is worse than a missing edge.
            diagnostics.append(Diagnostic(
                artifact_id, "WARNING", "UNRESOLVED_REFERENCE_SUBJECT",
                f"{rr.predicate} to {rr.to_name!r} is attributed to {rr.from_qname!r}, which "
                f"has {index.defines(rr.from_qname)} occurrences and none enclosing this site; "
                f"edge dropped rather than misattributed",
                rl.get("line_start")))
            continue

        quoted = data[rl["byte_start"]:rl["byte_end"]].decode("utf-8")
        object_qn = rr.to_qname if rr.resolution in (
            Resolution.DETERMINISTIC, Resolution.HEURISTIC) else None
        object_sid = None
        resolution, reason = rr.resolution.value, rr.reason
        if object_qn:
            local = index.defines(object_qn)
            if local == 1:
                object_sid = index.sole_definition(object_qn)
            elif local == 0:
                # not defined here: a corpus-wide target, resolved by name
                object_sid = gsym.get(object_qn)
            if object_sid is None and (local > 1 or
                                       (local == 0 and object_qn in gsym)):
                # Several symbols carry this name -- here, or in the artifact
                # that owns it. Do not fall through to an arbitrary one; a
                # reference target has no source span to disambiguate it, and a
                # wrong edge is worse than an unresolved one.
                where = "this artifact" if local > 1 else "the corpus"
                resolution = Resolution.UNRESOLVED.value
                reason = (f"{object_qn!r} has several definitions in {where}; "
                          f"which one is meant is not statically determinable")

        cid = emit(rr.predicate, subject_sid, object_sid,
                   None if object_sid else rr.to_name, rr.locator, quoted)
        # The `reference` table records how well a target SYMBOL was resolved. A
        # literal-valued claim has no target symbol, so it gets no row: writing
        # one would assert DETERMINISTIC resolution with a null object_id, which
        # check_invariants correctly reports as an unsupported assertion.
        if cid and rr.predicate not in LITERAL_OBJECT:
            refs.append((cid, rr.to_name, resolution, reason))
    return symbols, claims, refs, diagnostics
