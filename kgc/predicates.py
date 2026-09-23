"""Canonical predicate vocabulary.

One predicate, one meaning. Only predicates the measured corpora actually
require are defined; the vocabulary is not padded for features that do not
exist.
"""
from __future__ import annotations

from dataclasses import dataclass


# Cardinality decides whether two differing values are a contradiction or simply
# two facts. A multi-valued predicate can never contradict itself by having more
# than one object: `f()` calling both `a()` and `b()` is two facts, not a dispute.
FUNCTIONAL = "FUNCTIONAL"          # at most one value per (subject, scope)
MULTI_VALUED = "MULTI_VALUED"      # many values are normal and additive


@dataclass(frozen=True)
class PredicateSpec:
    name: str
    meaning: str
    subject_type: str
    object_type: str
    structural: bool            # parser-derivable, not model-assertable
    deterministic: bool         # a deterministic analyser can establish it today
    allowed_establishment: tuple[str, ...]
    cardinality: str            # FUNCTIONAL | MULTI_VALUED
    emitted_by_compiler: bool   # does any adapter actually produce this today?

    @property
    def may_contradict(self) -> bool:
        """Only a functional predicate can be contradicted by a differing value."""
        return self.cardinality == FUNCTIONAL


_S = ("DERIVED", "CONFIRMED")                       # structural: never model-only
_SEM = ("DERIVED", "CONFIRMED", "PROPOSED", "DISPUTED")

CANONICAL: dict[str, PredicateSpec] = {
    # ---- structural, multi-valued: many objects are normal and additive ----
    "CONTAINS":  PredicateSpec("CONTAINS", "subject lexically encloses object",
                               "symbol", "symbol", True, True, _S, MULTI_VALUED, True),
    "DEFINES":   PredicateSpec("DEFINES", "subject artifact introduces object symbol",
                               "artifact", "symbol", True, True, _S, MULTI_VALUED, False),
    "CALLS":     PredicateSpec("CALLS", "subject contains a call site targeting object",
                               "symbol", "symbol|literal", True, True, _S, MULTI_VALUED, True),
    "IMPORTS":   PredicateSpec("IMPORTS", "subject module binds a name from object",
                               "symbol", "symbol|literal", True, True, _S, MULTI_VALUED, True),
    # Python permits multiple inheritance -- `class C(A, B)` is two EXTENDS facts,
    # not a dispute. Treating it as functional would reproduce the very defect
    # this gate corrects, in a predicate the instruction's example list omitted.
    "EXTENDS":   PredicateSpec("EXTENDS", "subject class derives from object class",
                               "symbol", "symbol|literal", True, True, _S, MULTI_VALUED, True),
    "READS":     PredicateSpec("READS", "subject reads object's value",
                               "symbol", "symbol", True, True, _S, MULTI_VALUED, False),
    "WRITES":    PredicateSpec("WRITES", "subject assigns object's value",
                               "symbol", "symbol", True, True, _S, MULTI_VALUED, False),
    # ---- functional: at most one value per subject and scope ----
    "HAS_DEFAULT": PredicateSpec("HAS_DEFAULT", "subject's default value is object",
                                 "symbol", "literal", True, True, _S, FUNCTIONAL, False),
    # The one functional predicate the compiler actually emits (K-1): a direct
    # class-body assignment of a bare literal. See kgc/analysis/python_backend.py.
    "HAS_VALUE": PredicateSpec("HAS_VALUE", "subject's literal value is object",
                               "symbol", "literal", True, True, _S, FUNCTIONAL, True),
    "HAS_TYPE":  PredicateSpec("HAS_TYPE", "subject's declared type is object",
                               "symbol", "symbol|literal", True, False, _S, FUNCTIONAL, False),
    # A symbol has ONE documented purpose; two sources asserting different
    # purposes is a real dispute, so this stays functional despite being semantic.
    "HAS_PURPOSE": PredicateSpec("HAS_PURPOSE", "subject's documented purpose is object",
                                 "symbol", "literal", False, False, _SEM, FUNCTIONAL, False),
}

STRUCTURAL = frozenset(p for p, s in CANONICAL.items() if s.structural)
SEMANTIC = frozenset(p for p, s in CANONICAL.items() if not s.structural)
# Predicates whose object is a VALUE, not a reference to another symbol. They
# carry object_literal and never object_id, and there is nothing in them for the
# cross-module resolver to resolve.
LITERAL_OBJECT = frozenset(p for p, s in CANONICAL.items() if s.object_type == "literal")

# Establishment levels that may support an exposed answer. This is NOT the same
# question as `allowed_establishment`, and collapsing the two would weaken the
# trust boundary rather than simplify it:
#
#   allowed_establishment  what may be STORED for this predicate. HAS_PURPOSE
#                          permits PROPOSED, so a model may record one.
#   TRUSTED_ESTABLISHMENT  what may be ANSWERED WITH. A model proposal is
#                          storable and queryable but never a trusted answer.
#
# They coincide for structural predicates and diverge for semantic ones. Both
# live here so the support layer holds no establishment policy of its own.
TRUSTED_ESTABLISHMENT = frozenset({"DERIVED", "CONFIRMED"})

# Natural-language property words -> canonical predicate. Deliberately tiny and
# explicit: an unmapped property word is reported unmapped, never guessed.
PROPERTY_TO_PREDICATE: dict[str, str] = {
    "default": "HAS_DEFAULT", "defaults": "HAS_DEFAULT",
    "value": "HAS_VALUE", "type": "HAS_TYPE",
    "purpose": "HAS_PURPOSE", "rationale": "HAS_PURPOSE",
}


def may_contradict(pred: str) -> bool:
    """Whether a differing value for this predicate can mean contradiction.

    Unknown predicates are conservatively treated as MULTI_VALUED: inventing a
    contradiction is worse than missing one, because a false CONTRADICTS
    presents two correct facts as a dispute.
    """
    spec = CANONICAL.get(pred)
    return bool(spec) and spec.may_contradict


def cardinality(pred: str) -> str:
    spec = CANONICAL.get(pred)
    return spec.cardinality if spec else MULTI_VALUED


def allows(pred: str, establishment: str) -> bool:
    """Whether this predicate may be established at this level."""
    spec = CANONICAL.get(pred)
    return bool(spec) and establishment in spec.allowed_establishment


def is_trusted(pred: str, establishment: str) -> bool:
    """The ONLY authority on whether a claim's establishment may be trusted.

    Both conditions come from this module, so changing a predicate's definition
    changes the trust decision with no second set to keep in step:

      * the level must be trustworthy at all (TRUSTED_ESTABLISHMENT), and
      * the predicate's own spec must permit it at that level.

    A predicate outside the vocabulary falls back to the first rule alone: it is
    not defined here, so this module has nothing more specific to say about it.
    """
    if establishment not in TRUSTED_ESTABLISHMENT:
        return False
    spec = CANONICAL.get(pred)
    return establishment in spec.allowed_establishment if spec else True
