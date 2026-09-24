"""Typed evidence verification.

One verifier per locator kind. The strength a verifier may assign is bounded by
what the modality permits -- the system never marks evidence VERIFIED beyond
what it can actually perform (ADR-0006 Part 3).

Two rules with no exceptions:
  * Evidence is never invented.
  * Invalid evidence is never silently repaired. A near-miss quotation is a
    rejection, not something to fuzzy-match onto nearby text.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone

from kgc.ids import content_sha256, evidence_id
from kgc.ir import Evidence, Locator, LocatorKind, VerificationStrength
from kgc.safety import parse_json_hardened, parse_xml_hardened, xml_itertext


class EvidenceError(Exception):
    """Evidence could not be verified. Never caught to 'repair' the evidence."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_evidence(artifact_id: str, artifact_bytes: bytes, locator: Locator,
                  quoted_text: str) -> Evidence:
    """Build an *unverified* evidence record. Verification is a separate step."""
    sha = content_sha256(artifact_bytes)
    return Evidence(
        evidence_id=evidence_id(artifact_id, sha, locator.kind.value,
                                locator.payload, quoted_text),
        artifact_id=artifact_id, artifact_sha256=sha,
        locator=locator, quoted_text=quoted_text)


def verify(ev: Evidence, artifact_bytes: bytes) -> Evidence:
    """Re-derive the cited content from the artifact and compare.

    Raises EvidenceError on any mismatch. Returns a new Evidence carrying the
    strength actually achieved.
    """
    actual_sha = content_sha256(artifact_bytes)
    if actual_sha != ev.artifact_sha256:
        raise EvidenceError(
            f"artifact content drifted: evidence pinned {ev.artifact_sha256[:12]}, "
            f"artifact is {actual_sha[:12]}")

    kind = ev.locator.kind
    verifier = _VERIFIERS.get(kind)
    if verifier is None:
        raise EvidenceError(f"no verifier for locator kind {kind.value}; "
                            f"refusing to assert verification")
    strength, engine = verifier(ev, artifact_bytes)
    return Evidence(
        evidence_id=ev.evidence_id, artifact_id=ev.artifact_id,
        artifact_sha256=ev.artifact_sha256, locator=ev.locator,
        quoted_text=ev.quoted_text, verification_strength=strength,
        verifier_engine=engine, verified_at=_now())


# ── per-kind verifiers ────────────────────────────────────────────────────

def _verify_byte_range(ev: Evidence, data: bytes):
    p = ev.locator.payload
    s, e = p["byte_start"], p["byte_end"]
    if not (0 <= s <= e <= len(data)):
        raise EvidenceError(f"byte range [{s},{e}] outside artifact of {len(data)} bytes")
    try:
        actual = data[s:e].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError(f"byte range does not decode as UTF-8: {exc}") from None
    if actual != ev.quoted_text:
        raise EvidenceError(
            f"quoted text does not match source at [{s},{e}]: "
            f"expected {ev.quoted_text[:40]!r}, found {actual[:40]!r}")
    return VerificationStrength.EXACT, "byte_compare/1"


def _verify_ast_node(ev: Evidence, data: bytes):
    """Structural identity: re-parse and confirm the node exists at that path.

    Survives reformatting, which byte ranges do not. Still EXACT: the node is
    genuinely re-derived from the artifact.
    """
    p = ev.locator.payload
    try:
        tree = ast.parse(data.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise EvidenceError(f"artifact no longer parses: {exc}") from None

    node: ast.AST | None = tree
    for step in [s for s in p["path"].split("/") if s]:
        node = _child_named(node, step)
        if node is None:
            raise EvidenceError(f"AST path {p['path']!r} does not resolve (missing {step!r})")
    if type(node).__name__ != p["node_type"]:
        raise EvidenceError(
            f"AST node type mismatch: expected {p['node_type']}, found {type(node).__name__}")
    name = getattr(node, "name", None)
    if name is not None and ev.quoted_text and name != ev.quoted_text:
        raise EvidenceError(f"AST node name mismatch: {name!r} != {ev.quoted_text!r}")
    return VerificationStrength.EXACT, "ast_reparse/1"


def _child_named(node: ast.AST, step: str):
    for child in ast.iter_child_nodes(node):
        if f"{type(child).__name__}:{getattr(child, 'name', '')}" == step:
            return child
    return None


def _verify_json_pointer(ev: Evidence, data: bytes):
    doc = parse_json_hardened(data)
    cur = doc
    for tok in [t for t in ev.locator.payload["pointer"].split("/") if t != ""]:
        tok = tok.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            if not tok.isdigit() or int(tok) >= len(cur):
                raise EvidenceError(f"JSON pointer index {tok!r} out of range")
            cur = cur[int(tok)]
        elif isinstance(cur, dict):
            if tok not in cur:
                raise EvidenceError(f"JSON pointer key {tok!r} not present")
            cur = cur[tok]
        else:
            raise EvidenceError(f"JSON pointer descends into scalar at {tok!r}")
    if str(cur) != ev.quoted_text:
        raise EvidenceError(f"JSON value mismatch: {str(cur)[:40]!r} != {ev.quoted_text[:40]!r}")
    return VerificationStrength.EXACT, "json_pointer/1"


def _verify_xml_path(ev: Evidence, data: bytes):
    """Uses the hardened, non-evaluating parser -- verification must not be a
    vector for entity resolution (risk R-19)."""
    root = parse_xml_hardened(data)
    steps = [s for s in ev.locator.payload["path"].split("/") if s]
    node = root
    if steps and steps[0] == root["tag"]:
        steps = steps[1:]
    for step in steps:
        tag, _, idx = step.partition("[")
        i = int(idx.rstrip("]")) - 1 if idx else 0
        matches = [c for c in node["children"] if c["tag"] == tag]
        if i >= len(matches):
            raise EvidenceError(f"XML path step {step!r} does not resolve")
        node = matches[i]
    if xml_itertext(node).strip() != ev.quoted_text.strip():
        raise EvidenceError("XML text mismatch at path")
    return VerificationStrength.EXACT, "xml_hardened/1"


def _verify_document(ev: Evidence, data: bytes):
    """Re-extract the unit with the pinned library and compare its text.

    This is REPRODUCIBLE, never EXACT. A PDF's file bytes are compressed and
    carry no reader-visible offsets, so nothing is byte-compared here; what is
    proven is that running the same extractor over the same artifact yields the
    same text for the same page. Claiming EXACT would assert a comparison that
    did not happen.
    """
    from kgc.analysis.document import extracted_text
    p = ev.locator.payload
    suffix = ".pdf" if ev.locator.kind is LocatorKind.PDF_BOX else ".docx"
    try:
        units = extracted_text(data, suffix)
    except ImportError as exc:
        raise EvidenceError(f"the {suffix} adapter is unavailable: {exc}") from None
    except Exception as exc:
        raise EvidenceError(f"artifact no longer extracts: {type(exc).__name__}: {exc}") from None

    if ev.locator.kind is LocatorKind.PDF_BOX:
        key, where = p["page"], f"page {p['page']}"
    else:
        key, where = (p.get("unit", "paragraph"), p["para"]), f"{p.get('unit')} {p['para']}"
    if key not in units:
        raise EvidenceError(f"{where} is no longer present in the document")
    actual = units[key] if suffix == ".pdf" else (units[key] or "").strip()
    if actual != ev.quoted_text:
        raise EvidenceError(
            f"re-extracted text for {where} differs from the stored quotation: "
            f"expected {ev.quoted_text[:40]!r}, found {actual[:40]!r}")
    return VerificationStrength.REPRODUCIBLE, f"{suffix.lstrip('.')}_reextract/1"


_VERIFIERS = {
    LocatorKind.BYTE_RANGE: _verify_byte_range,
    LocatorKind.AST_NODE: _verify_ast_node,
    LocatorKind.JSON_POINTER: _verify_json_pointer,
    LocatorKind.XML_PATH: _verify_xml_path,
    LocatorKind.PDF_BOX: _verify_document,
    LocatorKind.DOCX_PARA: _verify_document,
    # IMAGE_BOX / AUDIO_SPAN deliberately absent: no verifier means verify()
    # refuses rather than asserting a strength it cannot support.
}
