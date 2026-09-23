"""Canonical predicate vocabulary.

One predicate, one meaning. Only predicates the measured corpora actually
require are defined; the vocabulary is not padded for features that do not
exist.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PredicateSpec:
    name: str
    meaning: str
    subject_type: str
    object_type: str
    structural: bool            # parser-derivable, not model-assertable
    deterministic: bool         # a deterministic analyser can establish it today
    allowed_establishment: tuple[str, ...]


_S = ("DERIVED", "CONFIRMED")                       # structural: never model-only
_SEM = ("DERIVED", "CONFIRMED", "PROPOSED", "DISPUTED")

CANONICAL: dict[str, PredicateSpec] = {
    "CONTAINS":  PredicateSpec("CONTAINS", "subject lexically encloses object",
                               "symbol", "symbol", True, True, _S),
    "DEFINES":   PredicateSpec("DEFINES", "subject artifact introduces object symbol",
                               "artifact", "symbol", True, True, _S),
    "CALLS":     PredicateSpec("CALLS", "subject contains a call site targeting object",
                               "symbol", "symbol|literal", True, True, _S),
    "IMPORTS":   PredicateSpec("IMPORTS", "subject module binds a name from object",
                               "symbol", "symbol|literal", True, True, _S),
    "EXTENDS":   PredicateSpec("EXTENDS", "subject class derives from object class",
                               "symbol", "symbol|literal", True, True, _S),
    "READS":     PredicateSpec("READS", "subject reads object's value",
                               "symbol", "symbol", True, True, _S),
    "WRITES":    PredicateSpec("WRITES", "subject assigns object's value",
                               "symbol", "symbol", True, True, _S),
    "HAS_DEFAULT": PredicateSpec("HAS_DEFAULT", "subject's default value is object",
                                 "symbol", "literal", True, True, _S),
    "HAS_VALUE": PredicateSpec("HAS_VALUE", "subject's literal value is object",
                               "symbol", "literal", True, True, _S),
    "HAS_TYPE":  PredicateSpec("HAS_TYPE", "subject's declared type is object",
                               "symbol", "symbol|literal", True, False, _S),
    "HAS_PURPOSE": PredicateSpec("HAS_PURPOSE", "subject's documented purpose is object",
                                 "symbol", "literal", False, False, _SEM),
}

STRUCTURAL = frozenset(p for p, s in CANONICAL.items() if s.structural)
SEMANTIC = frozenset(p for p, s in CANONICAL.items() if not s.structural)

# Natural-language property words -> canonical predicate. Deliberately tiny and
# explicit: an unmapped property word is reported unmapped, never guessed.
PROPERTY_TO_PREDICATE: dict[str, str] = {
    "default": "HAS_DEFAULT", "defaults": "HAS_DEFAULT",
    "value": "HAS_VALUE", "type": "HAS_TYPE",
    "purpose": "HAS_PURPOSE", "rationale": "HAS_PURPOSE",
}


def is_canonical(pred: str) -> bool:
    return pred in CANONICAL


def allows(pred: str, establishment: str) -> bool:
    """Whether this predicate may be established at this level."""
    spec = CANONICAL.get(pred)
    return bool(spec) and establishment in spec.allowed_establishment
