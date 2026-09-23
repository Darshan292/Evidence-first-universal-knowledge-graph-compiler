"""Query constraint IR (QUERY_CONSTRAINT_IR.md).

Rule-based. No LLM, no learned model, no synonym table.

A partially understood query must NEVER silently become a different query: that
is how "what port does TimestampSigner listen on" once returned the class's
definition as though it were a port. `parse_status` makes the distinction
explicit and the support layer refuses anything that is not PARSED.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from kgc.predicates import PROPERTY_TO_PREDICATE


class ParseStatus(str, Enum):
    PARSED = "PARSED"              # every field needed by the query shape is present
    PARTIAL = "PARTIAL"            # a field is recognised but incomplete
    UNPARSEABLE = "UNPARSEABLE"    # no rule applies
    AMBIGUOUS = "AMBIGUOUS"        # more than one reading applies


IDENT = re.compile(r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*\b")
NUM = re.compile(r"\b\d+(?:\.\d+)?\b")
QUOTED = re.compile(r"'([^']+)'|\"([^\"]+)\"")
FILE_SCOPE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*\.(?:py|rst|md|toml|txt|html))\b")
INTERROGATIVE = re.compile(r"\b(what|which|why|how|where|when|does|do|is|are|can)\b", re.I)

RELATION_PATTERNS = [
    (re.compile(r"\bdoes\s+([\w.]+)\s+(?:call|invoke)\s+([\w.]+)", re.I), "CALLS", 2),
    (re.compile(r"\bwhat\s+does\s+([\w.]+)\s+call\b", re.I), "CALLS", 1),
    (re.compile(r"\bwhich\s+method\s+of\s+([\w.]+)\s+calls?\s+([\w.]+)", re.I), "CALLS", 2),
    (re.compile(r"\bdoes\s+([\w.]+)\s+(?:extend|subclass|inherit\s+from)\s+([\w.]+)", re.I), "EXTENDS", 2),
    (re.compile(r"\bwhich\s+class\s+does\s+([\w.]+)\s+extend\b", re.I), "EXTENDS", 1),
    (re.compile(r"\bwhat\s+does\s+([\w.]+)\s+inherit\s+from\b", re.I), "EXTENDS", 1),
    (re.compile(r"\bdoes\s+([\w.]+)\s+import\s+([\w.]+)", re.I), "IMPORTS", 2),
    (re.compile(r"\bwhat\s+does\s+([\w.]+)\s+import\b", re.I), "IMPORTS", 1),
    (re.compile(r"\bwhat\s+does\s+([\w.]+)\s+contain\b", re.I), "CONTAINS", 1),
]

PROPERTY_PATTERNS = [
    re.compile(r"what\s+(?:is|are)\s+the\s+(?P<prop>[\w_ ]+?)\s+(?:of|for)\s+(?P<ent>[\w.]+)", re.I),
    re.compile(r"(?P<ent>[\w.]+)'s\s+(?P<prop>[\w_]+)", re.I),
    re.compile(r"what\s+(?:is|are)\s+the\s+default\s+(?P<prop>[\w_ ]+?)\s+(?:of|for|in)\s+(?P<ent>[\w.]+)", re.I),
]

STOP_ENTITY = {"the", "a", "an", "it", "this", "that", "default", "value", "class",
               "module", "method", "function", "file", "library", "attribute", "property"}


@dataclass
class QueryConstraints:
    raw: str
    parse_status: ParseStatus = ParseStatus.UNPARSEABLE
    subject: str | None = None
    subjects: list[str] = field(default_factory=list)
    predicate: str | None = None        # canonical predicate when determined
    prop_word: str | None = None        # the natural-language property word
    obj: str | None = None
    literal: list[str] = field(default_factory=list)
    operator: str | None = None
    source_scope: str | None = None
    temporal_scope: str | None = None
    shape: str = "unparsed"

    @property
    def parsed(self) -> bool:
        return self.parse_status is ParseStatus.PARSED

    def as_dict(self):
        d = dict(self.__dict__)
        d["parse_status"] = self.parse_status.value
        return d


def extract(query: str) -> QueryConstraints:
    c = QueryConstraints(raw=query)
    c.literal = sorted({*NUM.findall(query), *{a or b for a, b in QUOTED.findall(query)}})
    m = FILE_SCOPE.search(query)
    if m:
        c.source_scope = m.group(1)

    idents = [i for i in IDENT.findall(query)
              if i.lower() not in STOP_ENTITY
              and (any(ch.isupper() for ch in i) or "_" in i or "." in i)
              and not FILE_SCOPE.fullmatch(i)]

    for pat, pred, arity in RELATION_PATTERNS:
        mm = pat.search(query)
        if mm:
            g = [x.strip(".,?") for x in mm.groups() if x]
            c.predicate, c.subject, c.shape = pred, g[0], "relation"
            if arity == 2 and len(g) > 1:
                c.obj = g[1]
            c.subjects = [g[0]]
            c.parse_status = ParseStatus.PARSED
            return c

    for pat in PROPERTY_PATTERNS:
        mm = pat.search(query)
        if mm:
            d = mm.groupdict()
            prop = (d.get("prop") or "").strip().lower()
            ent = (d.get("ent") or "").strip(".,?")
            c.prop_word = prop.replace(" ", "_") or None
            c.shape = "property"
            if ent and ent.lower() not in STOP_ENTITY:
                c.subject, c.subjects = ent, [ent]
            elif idents:
                c.subject, c.subjects = idents[0], idents[:1]
            head = (prop.split("_")[0] if prop else "")
            c.predicate = PROPERTY_TO_PREDICATE.get(head)
            c.parse_status = (ParseStatus.PARSED if (c.subject and c.prop_word)
                              else ParseStatus.PARTIAL)
            return c

    if idents and not INTERROGATIVE.search(query):
        c.subject, c.subjects = idents[0], idents[:2]
        c.shape = "identifier"
        c.parse_status = (ParseStatus.AMBIGUOUS if len(set(idents)) > 1
                          else ParseStatus.PARSED)
        return c

    if idents:
        # asks about something, but no property or relation was extractable
        c.subjects = idents[:2]
        c.subject = idents[0]
        c.shape = "interrogative_unmapped"
        c.parse_status = ParseStatus.PARTIAL
        return c

    c.shape = "unparsed"
    c.parse_status = ParseStatus.UNPARSEABLE
    return c
