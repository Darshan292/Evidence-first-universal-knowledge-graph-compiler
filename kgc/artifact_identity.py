"""Canonical artifact identity. THE single source of truth for path semantics.

Suffix matching (`rel_path.endswith(scope)`) let `utils.py` satisfy a scope
constraint against `src/utils.py`, `tests/utils.py` and `vendor/utils.py`
simultaneously -- and because a scope was supplied, the ambiguity guard was
skipped, so one was chosen by row order.

Canonical form: corpus-relative, forward-slash separated, no leading `./`,
compared EXACTLY and case-sensitively.
"""
from __future__ import annotations

from pathlib import PurePath


def canonical_path(rel_path: str) -> str:
    """Normalize a corpus-relative path to its canonical identity."""
    s = str(rel_path).replace("\\", "/").strip()
    while s.startswith("./"):
        s = s[2:]
    parts = [p for p in s.split("/") if p not in ("", ".")]
    return "/".join(parts)


def is_exact(artifact_path: str, scope: str) -> bool:
    """Exact canonical identity. No suffix, prefix or fuzzy matching."""
    return canonical_path(artifact_path) == canonical_path(scope)


def resolve_scope(scope: str, known_paths) -> tuple[list[str], str]:
    """Resolve a user-supplied scope to canonical artifact paths.

    Returns (matches, status) where status is:
      EXACT      the scope names exactly one artifact
      AMBIGUOUS  a bare basename matching several artifacts -- caller must abstain
      UNKNOWN    the scope names no artifact in the corpus
    """
    want = canonical_path(scope)
    known = [canonical_path(p) for p in known_paths]

    exact = [p for p in known if p == want]
    if exact:
        return sorted(set(exact)), "EXACT"

    # A bare basename is a convenience, never an identity. It resolves only when
    # it is unambiguous; otherwise the caller must abstain rather than choose.
    if "/" not in want:
        by_base = sorted({p for p in known if PurePath(p).name == want})
        if len(by_base) == 1:
            return by_base, "EXACT"
        if len(by_base) > 1:
            return by_base, "AMBIGUOUS"
    return [], "UNKNOWN"
