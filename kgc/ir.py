"""Canonical intermediate representation.

Language- and parser-independent. The IR records *what an analyser observed at a
location*; it carries no semantics, no inference and no confidence. Those live in
Claim, which is downstream and evidence-bound.

Extensibility contract (ADR-0002): a new modality or backend adds LocatorKind
values and an EvidenceVerifier, never columns and never new IR record types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Modality(str, Enum):
    CODE = "code"
    # DOCUMENT / STRUCTURED / image / audio / video arrive with their adapters.
    # Declaring them now would be a value nothing can produce -- adding an enum
    # member later is trivial, so the speculative version earns nothing.


class ParseStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    UNSUPPORTED = "UNSUPPORTED"   # recognised, no analyser -- never silently absent


class LocatorKind(str, Enum):
    BYTE_RANGE = "byte_range"     # code: bytes + line/col
    AST_NODE = "ast_node"         # code: structural identity, survives reformatting
    JSON_POINTER = "json_pointer"  # RFC 6901
    XML_PATH = "xml_path"         # restricted subset, non-evaluating parser
    PDF_BOX = "pdf_box"           # documents: page + offsets into extracted text
    DOCX_PARA = "docx_para"       # documents: paragraph/table index + offsets
    IMAGE_BOX = "image_box"
    AUDIO_SPAN = "audio_span"


class VerificationStrength(str, Enum):
    """How well evidence can be independently re-derived. Never a boast."""
    EXACT = "EXACT"                  # content re-derived and byte-compared
    REPRODUCIBLE = "REPRODUCIBLE"    # re-derivable via a pinned deterministic engine
    STRUCTURAL = "STRUCTURAL"        # locator is valid; content not re-derivable


class Lifecycle(str, Enum):
    CANDIDATE = "CANDIDATE"
    VALIDATING = "VALIDATING"
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    # NOTE: no query path filters on lifecycle. SUPERSEDED and contradicted
    # claims remain queryable -- see test_contradiction.py. Filtering here is
    # what caused defect D-3.


class Establishment(str, Enum):
    DERIVED = "DERIVED"        # a deterministic analyser proved it
    CONFIRMED = "CONFIRMED"    # proposed by a model, independently re-derived
    PROPOSED = "PROPOSED"      # model-produced, evidence-verified, not derivable
    DISPUTED = "DISPUTED"      # model proposal contradicts a deterministic result


class Resolution(str, Enum):
    """Quality of a reference resolution. UNRESOLVED is mandatory, not optional."""
    DETERMINISTIC = "DETERMINISTIC"
    HEURISTIC = "HEURISTIC"
    UNRESOLVED = "UNRESOLVED"


# Predicates the analysers own. A model may never establish these (ADR-0006).
STRUCTURAL_PREDICATES = frozenset({
    "CONTAINS", "DEFINES", "IMPORTS", "CALLS", "REFERENCES",
    "EXTENDS", "IMPLEMENTS", "READS", "WRITES",
})

SEMANTIC_PREDICATES = frozenset({
    "SEMANTICALLY_RELATED_TO", "DESCRIBES", "MOTIVATES", "RATIONALE_FOR", "MENTIONS",
})

# Rewrite map applied when a model emits a structural predicate: the claim is
# preserved at PROPOSED rather than discarded (ADR-0006 Part 2).
STRUCTURAL_TO_SEMANTIC = {p: "SEMANTICALLY_RELATED_TO" for p in STRUCTURAL_PREDICATES}


@dataclass(frozen=True)
class Locator:
    kind: LocatorKind
    payload: dict

    def __post_init__(self):
        required = {
            LocatorKind.BYTE_RANGE: {"byte_start", "byte_end", "line_start", "line_end"},
            LocatorKind.AST_NODE: {"path", "node_type"},
            LocatorKind.JSON_POINTER: {"pointer"},
            LocatorKind.XML_PATH: {"path"},
            # Documents. `page` / `para` is the unit a reader can actually find.
            # byte_start/byte_end index the EXTRACTED TEXT, not the file bytes --
            # a PDF's file bytes are compressed and have no reader-visible
            # offsets. The verifier re-extracts and compares, so the strength is
            # REPRODUCIBLE, never EXACT.
            LocatorKind.PDF_BOX: {"page", "byte_start", "byte_end"},
            LocatorKind.DOCX_PARA: {"para", "byte_start", "byte_end"},
        }.get(self.kind)
        if required is None:
            raise ValueError(f"locator kind {self.kind} is declared but not emitted in Gate 1")
        missing = required - set(self.payload)
        if missing:
            raise ValueError(f"{self.kind.value} locator missing fields: {sorted(missing)}")
        if self.kind in (LocatorKind.BYTE_RANGE, LocatorKind.PDF_BOX, LocatorKind.DOCX_PARA):
            if self.payload["byte_start"] > self.payload["byte_end"]:
                raise ValueError("byte_start > byte_end")


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    source_id: str
    rel_path: str
    sha256: str
    size_bytes: int
    media_type: str
    modality: Modality
    parse_status: ParseStatus
    parse_error: str | None = None
    parser_id: str | None = None
    parser_version: str | None = None


@dataclass(frozen=True)
class Symbol:
    """A named construct. Language-neutral: `kind` is an open vocabulary."""
    symbol_id: str
    artifact_id: str
    parent_id: str | None
    kind: str                # module|class|function|method|variable|constant
    name: str
    qualified_name: str
    locator: Locator
    docstring: str | None = None


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    artifact_id: str
    artifact_sha256: str
    locator: Locator
    quoted_text: str
    verification_strength: VerificationStrength | None = None
    verifier_engine: str | None = None
    verified_at: str | None = None


@dataclass(frozen=True)
class Claim:
    claim_id: str
    predicate: str
    subject_id: str
    subject_kind: str
    object_id: str | None
    object_kind: str | None
    object_literal: str | None
    lifecycle: Lifecycle
    establishment: Establishment
    confidence: float | None        # producer self-report. GATES NOTHING.
    extractor_id: str
    extractor_version: str
    model_id: str | None
    prompt_version: str | None
    schema_version: str
    run_id: str
    evidence_ids: list[str] = field(default_factory=list)
    rejected_reason: str | None = None


@dataclass
class Diagnostic:
    """A recorded non-answer. An unparseable file is a fact, not an absence."""
    artifact_id: str
    severity: str        # ERROR | WARNING | INFO
    code: str            # SYNTAX_ERROR | UNRESOLVED_REFERENCE | SKIPPED_OVERSIZE ...
    message: str
    line: int | None = None
