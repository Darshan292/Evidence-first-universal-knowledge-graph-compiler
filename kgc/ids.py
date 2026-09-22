"""Content-addressed, deterministic identifiers.

Identifier classes (see GATE_1_REPORT.md §6):

  CONTENT-ADDRESSED   derived purely from artifact bytes (``content_sha256``).
  DETERMINISTIC       derived from content plus the identity of everything that
                      produced it (extractor, version, model, prompt, schema).
                      Stable across runs given identical inputs and config.
  DATABASE-LOCAL      surrogate keys with no semantic meaning. None exist.
  NON-DETERMINISTIC   deliberately unique per run: ``run_id`` only.

The rule that matters: model identity participates in ``claim_id`` so two models
over identical bytes produce distinct, coexisting claims rather than one
silently overwriting the other.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

_SEP = "\x1f"  # unit separator: cannot occur in the hashed fields


def canonical_json(obj: Any) -> str:
    """Stable JSON: sorted keys, no incidental whitespace, fixed separators."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# 128 bits of SHA-256, hex-encoded. Measured (experiments/c1_identifier_storage.py):
# 42% smaller database and 2.5x insert throughput versus the full 64-char digest,
# at identical lookup latency, while staying readable in a sqlite3 shell and in
# JSON. A 16-byte BLOB is smaller still (62% reduction) but is opaque and cannot
# be embedded in the evidence_ids JSON array without encoding it back to hex.
# Collision bound: 2^64 identifiers before a 50% chance of one collision.
ID_HEX_CHARS = 32


def _h(*parts: Any) -> str:
    joined = _SEP.join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:ID_HEX_CHARS]


def content_sha256(data: bytes) -> str:
    """CONTENT-ADDRESSED. The identity of a byte sequence."""
    return hashlib.sha256(data).hexdigest()


def source_id(canonical_uri: str) -> str:
    return _h("source", canonical_uri)


def artifact_id(source: str, rel_path: str, sha: str) -> str:
    return _h("artifact", source, rel_path, sha)


def symbol_id(artifact: str, qualified_name: str, kind: str, byte_start: int, byte_end: int) -> str:
    return _h("symbol", artifact, qualified_name, kind, byte_start, byte_end)


def evidence_id(artifact: str, sha: str, locator_kind: str, locator: dict, quoted_text: str) -> str:
    return _h("evidence", artifact, sha, locator_kind, canonical_json(locator), quoted_text)


def entity_id(entity_type: str, normal_form: str) -> str:
    """Run-independent by design: the same entity in two runs is one entity."""
    return _h("entity", entity_type, normal_form)


def claim_id(
    *,
    predicate: str,
    subject_id: str,
    object_id: str | None,
    object_literal: str | None,
    extractor_id: str,
    extractor_version: str,
    model_id: str | None,
    prompt_version: str | None,
    schema_version: str,
    evidence_ids: list[str],
) -> str:
    return _h(
        "claim", predicate, subject_id,
        object_id or "", object_literal or "",
        extractor_id, extractor_version,
        model_id or "", prompt_version or "", schema_version,
        canonical_json(sorted(evidence_ids)),
    )


def diagnostic_id(artifact: str, severity: str, code: str, message: str, line) -> str:
    """DETERMINISTIC: the same finding on the same artifact is one row."""
    return _h("diagnostic", artifact, severity, code, message, line)


def config_hash(config: dict) -> str:
    return _h("config", canonical_json(config))


def run_id(software_version: str, cfg_hash: str, started_at: str) -> str:
    """NON-DETERMINISTIC by design: two runs must be distinguishable."""
    return _h("run", software_version, cfg_hash, started_at)
