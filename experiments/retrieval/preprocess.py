"""Query and document preprocessing. Fixed, general-purpose, not tuned.

No alias table, no synonym list, no query-specific rule. The stopword list is a
standard English list; identifier splitting is a mechanical camelCase/snake_case
rule. Anything beyond this would be a mechanism demonstration, not a benchmark.
"""
from __future__ import annotations

import re

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "before", "by", "do", "does",
    "for", "from", "how", "i", "in", "is", "it", "its", "long", "of", "on",
    "or", "our", "point", "s", "that", "the", "their", "then", "there", "this",
    "to", "up", "we", "what", "when", "where", "which", "who", "why", "will",
    "with", "you", "your", "us", "did", "was", "were", "has", "have", "had",
}

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def split_identifier(tok: str) -> list[str]:
    parts = [p for p in _CAMEL.sub(" ", tok).replace("_", " ").split() if p]
    return [p.lower() for p in parts]


def tokenize(text: str, *, split_ids: bool = True) -> list[str]:
    out: list[str] = []
    for m in _TOKEN.findall(text):
        low = m.lower()
        out.append(low)
        if split_ids:
            parts = split_identifier(m)
            if len(parts) > 1:
                out.extend(parts)
    return [t for t in out if t not in STOPWORDS and len(t) > 1]


def fts_query(text: str) -> str:
    """OR semantics. FTS5 MATCH defaults to implicit AND, which returns zero
    hits for any paraphrase that does not share every term (defect D-2)."""
    toks = {t for t in tokenize(text)}
    return " OR ".join(sorted(f'"{t}"' for t in toks)) if toks else '""'
