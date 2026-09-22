"""Ingestion and parsing security boundary.

Source material is DATA, never instructions. Nothing in this module executes,
evaluates, resolves, fetches or follows anything found inside a source artifact.

The boundary is enforced *before* any parser sees a byte, and again inside the
evidence verifier -- the verification path is security-critical (risk R-19).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 8 * 1024 * 1024
NULL_BYTE_SCAN = 8192


class UnsafePath(Exception):
    """Raised when a candidate path escapes its root or is otherwise unsafe."""


@dataclass(frozen=True)
class Rejection:
    code: str
    message: str


def resolve_within(root: Path, candidate: Path) -> Path:
    """Resolve `candidate` and prove it stays inside `root`.

    Defeats ``../`` traversal, absolute-path injection, and symlink escape --
    ``Path.resolve()`` follows symlinks, so a link pointing outside `root`
    resolves outside it and is rejected here.
    """
    root_r = root.resolve(strict=True)
    cand_r = (root_r / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        cand_r.relative_to(root_r)
    except ValueError:
        raise UnsafePath(f"path escapes root: {candidate}") from None
    return cand_r


def classify(path: Path, *, root: Path) -> tuple[bytes | None, Rejection | None]:
    """Return (bytes, None) if the file is safe to parse, else (None, Rejection).

    Every rejection is recorded as an artifact row with a reason. A rejected file
    is a fact about the corpus, never a silently skipped one.
    """
    try:
        real = resolve_within(root, path)
    except (UnsafePath, FileNotFoundError, RuntimeError) as e:
        return None, Rejection("PATH_UNSAFE", str(e))

    if real.is_symlink():                      # never followed during walk
        return None, Rejection("SYMLINK_REFUSED", f"symlink not followed: {path}")
    if not real.is_file():
        return None, Rejection("NOT_A_FILE", f"not a regular file: {path}")

    st = real.stat()
    if st.st_size > MAX_FILE_BYTES:
        return None, Rejection("OVERSIZE", f"{st.st_size} bytes exceeds {MAX_FILE_BYTES}")

    with open(real, "rb") as fh:
        head = fh.read(NULL_BYTE_SCAN)
        if b"\x00" in head:
            return None, Rejection("BINARY_AS_TEXT", "NUL byte in header; refusing to parse as text")
        rest = fh.read()

    data = head + rest
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, Rejection("UNDECODABLE", f"not valid UTF-8: {e}")
    return data, None


# Deterministic language identification by extension. An unknown extension is
# reported as unknown -- never guessed from content.
LANGUAGE_BY_SUFFIX = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".java": "java", ".go": "go", ".rs": "rust",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".rb": "ruby", ".php": "php", ".cs": "csharp", ".kt": "kotlin",
    ".swift": "swift", ".scala": "scala", ".sh": "shell", ".sql": "sql",
    ".md": "markdown", ".json": "json", ".xml": "xml", ".csv": "csv",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".html": "html",
}

ANALYSED_SUFFIXES = (".py",)          # what an analyser exists for today

IGNORED_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache"}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".so", ".dylib", ".dll", ".o", ".a",
                    ".zip", ".gz", ".tar", ".whl", ".png", ".jpg", ".pdf",
                    ".db", ".sqlite", ".lbug", ".wal", ".shm"}


def detect_language(path: Path) -> str | None:
    return LANGUAGE_BY_SUFFIX.get(path.suffix.lower())


def walk_corpus(root: Path, suffixes: tuple[str, ...] | None = None):
    """Yield EVERY candidate file, not only the analysable ones.

    A file in an unanalysed language must reach the pipeline so it can be
    recorded with an explicit UNSUPPORTED status. Filtering it out here is what
    made non-Python files invisible: the coverage report claimed total success
    on a corpus it had silently ignored.
    """
    root = root.resolve(strict=True)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames
                       if d not in IGNORED_DIRS and not d.startswith(".")
                       and not Path(dirpath, d).is_symlink()]
        for fn in sorted(filenames):           # sorted: deterministic ordering
            p = Path(dirpath, fn)
            if p.suffix.lower() in IGNORED_SUFFIXES:
                continue
            if suffixes is not None and p.suffix not in suffixes:
                continue
            yield p


# --------------------------------------------------------------------------
# Hardened structured-data parsing (risk R-19).
# Used by the XML/JSON evidence verifiers. Python's default XML stack resolves
# external entities; a hostile document could otherwise cause local file reads
# *during evidence verification*.
# --------------------------------------------------------------------------

def parse_xml_hardened(data: bytes):
    """Parse XML with DTDs, external entities and entity expansion disabled.

    Built directly on expat so the handlers can be controlled explicitly;
    ``xml.etree.ElementTree.XMLParser`` does not expose them. Returns a nested
    ``(tag, attrs, children, text)`` tree -- deliberately a plain data structure,
    not a live object graph.

    Any DTD, DOCTYPE or entity declaration is REJECTED rather than interpreted.
    """
    import xml.parsers.expat as expat

    parser = expat.ParserCreate()

    def _reject_doctype(*_a, **_k):
        raise ValueError("DOCTYPE/DTD rejected: entity resolution is disabled")

    def _reject_entity(*_a, **_k):
        raise ValueError("entity declaration rejected")

    parser.StartDoctypeDeclHandler = _reject_doctype
    parser.EntityDeclHandler = _reject_entity
    parser.UnparsedEntityDeclHandler = _reject_entity
    parser.NotationDeclHandler = _reject_entity
    # Never resolve an external reference. Returning 0 makes expat raise.
    parser.ExternalEntityRefHandler = lambda *a, **k: 0
    # A skipped/undefined entity must be an error, never silently empty text.
    parser.SkippedEntityHandler = _reject_entity

    root = None
    stack: list[dict] = []

    def start(tag, attrs):
        nonlocal root
        node = {"tag": tag, "attrs": dict(attrs), "children": [], "text": ""}
        if stack:
            stack[-1]["children"].append(node)
        elif root is None:
            root = node
        stack.append(node)

    def end(_tag):
        stack.pop()

    def chars(txt):
        if stack:
            stack[-1]["text"] += txt

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars

    try:
        parser.Parse(data, True)
    except ValueError:
        raise
    except expat.ExpatError as e:
        raise ValueError(f"malformed or rejected XML: {e}") from None
    if root is None:
        raise ValueError("no XML root element")
    return root


def xml_itertext(node: dict) -> str:
    """Concatenate text of a hardened-parse tree (for evidence comparison)."""
    out = [node.get("text", "")]
    for c in node.get("children", ()):
        out.append(xml_itertext(c))
    return "".join(out)


def parse_json_hardened(data: bytes, *, max_bytes: int = 4 * 1024 * 1024):
    """Parse JSON with a size bound. json.loads does not execute anything."""
    import json
    if len(data) > max_bytes:
        raise ValueError(f"JSON exceeds {max_bytes} bytes")
    return json.loads(data.decode("utf-8"))
