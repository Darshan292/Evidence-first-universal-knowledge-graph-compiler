"""Deterministic claim-value normalization for conflict detection.

Conflict must be decided on claim SEMANTICS, not on evidence wording. Two
sources phrasing the same value differently agree; two sources stating different
values contradict.

The normalizer handles a closed domain: integers and decimals (with an optional
recognised unit), quoted strings, identifiers, booleans and canonical symbol ids.
**Anything outside that domain returns UNRESOLVED rather than a guess.**
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SAME, DIFFERENT, UNRESOLVED = "SAME", "DIFFERENT", "UNRESOLVED"

_UNITS = {
    "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "ms": "ms", "millisecond": "ms", "milliseconds": "ms",
    "m": "min", "min": "min", "minute": "min", "minutes": "min",
    "h": "h", "hour": "h", "hours": "h",
    "b": "byte", "byte": "byte", "bytes": "byte",
}
_TO_BASE = {"ms": 0.001, "s": 1.0, "min": 60.0, "h": 3600.0}

_NUM_UNIT = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*([A-Za-z]+)?")
_QUOTED = re.compile(r"""(?:b|r|f)?["']([^"']*)["']""")
_BOOL = {"true": True, "false": False, "none": None, "null": None}


@dataclass(frozen=True)
class Value:
    kind: str            # number | string | identifier | boolean | none
    norm: object
    unit: str | None = None

    def key(self):
        if self.kind == "number" and self.unit in _TO_BASE:
            return ("number_base", round(float(self.norm) * _TO_BASE[self.unit], 9))
        return (self.kind, self.norm, self.unit)


def parse_value(text: str) -> Value | None:
    """Extract a single comparable value, or None when the text is outside the
    closed domain (which the caller must treat as UNRESOLVED, not as agreement)."""
    if text is None:
        return None
    t = text.strip()
    if not t:
        return None

    low = t.lower()
    if low in _BOOL:
        v = _BOOL[low]
        return Value("none", None) if v is None else Value("boolean", v)

    q = _QUOTED.search(t)
    if q and q.group(1) != "":
        inner = q.group(1)
        m = _NUM_UNIT.fullmatch(inner.strip())
        if not m:
            return Value("string", inner)

    nums = _NUM_UNIT.findall(t)
    if len(nums) == 1:
        raw, unit = nums[0]
        u = _UNITS.get((unit or "").lower()) if unit else None
        if unit and u is None and unit.lower() not in ("x",):
            # a number glued to an unrecognised word: not safely comparable
            return None
        n = float(raw)
        return Value("number", int(n) if n.is_integer() else n, u)
    if len(nums) > 1:
        return None                      # several numbers: ambiguous, do not guess

    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\-]*", t):
        return Value("identifier", t.split(".")[-1])
    return None


def compare(a: str | None, b: str | None) -> str:
    """SAME / DIFFERENT / UNRESOLVED for two claim values."""
    va, vb = parse_value(a or ""), parse_value(b or "")
    if va is None or vb is None:
        return UNRESOLVED
    if va.kind != vb.kind:
        if {va.kind, vb.kind} == {"number", "string"}:
            return UNRESOLVED
        return DIFFERENT
    if va.kind == "number":
        if (va.unit is None) != (vb.unit is None):
            # one carries a unit and the other does not: comparable only if equal
            return SAME if float(va.norm) == float(vb.norm) else UNRESOLVED
        if va.unit and vb.unit and (va.unit in _TO_BASE) != (vb.unit in _TO_BASE):
            return UNRESOLVED
    return SAME if va.key() == vb.key() else DIFFERENT
