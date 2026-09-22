"""Deterministic query -> typed constraints (design S-B).

Rule-based only. No LLM, no learned model, no synonym table. When a query does
not parse into constraints, that is reported as `parsed=False` and the caller
must abstain rather than guess — the coverage of these rules IS the deterministic
boundary this gate exists to locate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# An identifier-shaped token: CamelCase, snake_case, ALL_CAPS, dotted, or _private
IDENT = re.compile(r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*\b")
NUM = re.compile(r"\b\d+(?:\.\d+)?\b")
QUOTED = re.compile(r"'([^']+)'|\"([^\"]+)\"")
FILE_SCOPE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*\.(?:py|rst|md|toml|txt))\b")

RELATION_PATTERNS = [
    (re.compile(r"\bdoes\s+(\S+)\s+(?:call|invoke)\s+(\S+)", re.I), "CALLS"),
    (re.compile(r"\bwhat\s+does\s+(\S+)\s+call\b", re.I), "CALLS"),
    (re.compile(r"\bwhich\s+method\s+of\s+(\S+)\s+calls?\s+(\S+)", re.I), "CALLS"),
    (re.compile(r"\bdoes\s+(\S+)\s+(?:extend|subclass|inherit\s+from)\s+(\S+)", re.I), "EXTENDS"),
    (re.compile(r"\bwhich\s+class\s+does\s+(\S+)\s+extend\b", re.I), "EXTENDS"),
    (re.compile(r"\bwhat\s+does\s+(\S+)\s+inherit\s+from\b", re.I), "EXTENDS"),
    (re.compile(r"\bdoes\s+(\S+)\s+import\s+(\S+)", re.I), "IMPORTS"),
    (re.compile(r"\bimport\s+(\S+)\s+from\b", re.I), "IMPORTS"),
    (re.compile(r"\bre-?export(?:s)?\s+from\s+(\S+)", re.I), "IMPORTS"),
    (re.compile(r"\braise(?:s|d)?\s+on\b", re.I), "CALLS"),
]

# "what is the <property> of <entity>" and its common inversions
PROPERTY_PATTERNS = [
    re.compile(r"what\s+(?:is|are)\s+the\s+(?P<prop>[\w_ ]+?)\s+(?:of|for)\s+(?P<ent>\S+)", re.I),
    re.compile(r"(?P<ent>\S+)'s\s+(?P<prop>[\w_]+)", re.I),
    re.compile(r"what\s+(?:is|are)\s+the\s+default\s+(?P<prop>[\w_ ]+)", re.I),
    re.compile(r"what\s+(?P<prop>[\w_]+)\s+does\s+(?P<ent>\S+)\s+use", re.I),
]

STOP_ENTITY = {"the", "a", "an", "it", "this", "that", "default", "value", "class",
               "module", "method", "function", "file", "library"}


@dataclass
class Constraints:
    raw: str
    parsed: bool = False
    entities: list[str] = field(default_factory=list)
    prop: str | None = None
    relation: str | None = None
    relation_object: str | None = None
    literals: list[str] = field(default_factory=list)
    source_scope: str | None = None
    shape: str = "unparsed"

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items()}


def extract(query: str) -> Constraints:
    c = Constraints(raw=query)
    c.literals = sorted({*NUM.findall(query),
                         *{a or b for a, b in QUOTED.findall(query)}})
    m = FILE_SCOPE.search(query)
    if m:
        c.source_scope = m.group(1)

    idents = [i for i in IDENT.findall(query)
              if i.lower() not in STOP_ENTITY
              and (any(ch.isupper() for ch in i) or "_" in i or "." in i)]
    idents = [i for i in idents if not FILE_SCOPE.fullmatch(i)]

    for pat, rel in RELATION_PATTERNS:
        m = pat.search(query)
        if m:
            c.relation = rel
            groups = [g.strip(".,?") for g in m.groups() if g]
            if groups:
                c.entities = [groups[0]]
                if len(groups) > 1:
                    c.relation_object = groups[1]
            c.parsed = True
            c.shape = "relation"
            break

    if not c.parsed:
        for pat in PROPERTY_PATTERNS:
            m = pat.search(query)
            if m:
                d = m.groupdict()
                prop = (d.get("prop") or "").strip()
                ent = (d.get("ent") or "").strip(".,?")
                c.prop = prop.replace(" ", "_") if prop else None
                if ent and ent.lower() not in STOP_ENTITY:
                    c.entities = [ent]
                elif idents:
                    c.entities = idents[:1]
                c.parsed = bool(c.prop)
                c.shape = "property"
                break

    INTERROGATIVE = re.compile(r"\b(what|which|why|how|where|when|does|do|is|are)\b", re.I)
    if not c.parsed and idents:
        if INTERROGATIVE.search(query):
            # asks about something, but no property or relation could be
            # extracted -- degrading to identifier lookup answers a different
            # question than the one asked.
            c.entities = idents[:2]
            c.shape = "unparsed_interrogative"
        else:
            c.entities = idents[:2]
            c.parsed = True
            c.shape = "identifier"

    if not c.entities and idents:
        c.entities = idents[:2]
    return c
