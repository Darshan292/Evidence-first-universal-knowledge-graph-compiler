"""Corpus -> retrieval chunks. Reads the corpus only. Never reads gold.

A chunk is the unit a retriever returns: (rel_path, span, text). Code chunks are
symbols taken from the compiled graph, so a retrieval hit can be expanded through
real structural edges. Document chunks are blocks. Structured-data chunks are
records.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Chunk:
    chunk_id: str
    rel_path: str
    kind: str            # symbol | block | record
    text: str
    byte_start: int
    byte_end: int
    symbol_qname: str | None = None


def _blocks(text: str):
    """Split a document into blocks on blank lines, keeping byte offsets."""
    out, pos = [], 0
    for raw in text.split("\n\n"):
        start = text.index(raw, pos) if raw else pos
        if raw.strip():
            out.append((start, start + len(raw), raw))
        pos = start + len(raw)
    return out


def build(corpus_root: Path, store=None) -> list[Chunk]:
    chunks: list[Chunk] = []
    sym_by_path: dict[str, list] = {}
    if store is not None:
        for r in store.con.execute(
            "SELECT a.rel_path, s.qualified_name, s.kind, s.locator"
            "  FROM symbol s JOIN artifact a USING(artifact_id) ORDER BY a.rel_path"):
            sym_by_path.setdefault(r["rel_path"], []).append(
                (r["qualified_name"], r["kind"], json.loads(r["locator"])))

    for path in sorted(corpus_root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(corpus_root))
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue

        if path.suffix == ".py":
            syms = sym_by_path.get(rel, [])
            for qname, kind, loc in syms:
                s, e = loc["byte_start"], loc["byte_end"]
                body = raw[s:e].decode("utf-8", errors="replace")
                chunks.append(Chunk(f"{rel}#{qname}", rel, "symbol", body, s, e, qname))
            if not syms:
                chunks.append(Chunk(f"{rel}#0", rel, "block", text, 0, len(raw)))
        elif path.suffix in (".md", ".txt"):
            for i, (s, e, body) in enumerate(_blocks(text)):
                chunks.append(Chunk(f"{rel}#{i}", rel, "block", body, s, e))
        elif path.suffix == ".json":
            doc = json.loads(text)
            for pointer, value in _flatten_json(doc):
                frag = f"{pointer} = {value}"
                idx = text.find(str(value))
                chunks.append(Chunk(f"{rel}#{pointer}", rel, "record", frag,
                                    max(idx, 0), max(idx, 0) + len(str(value))))
            chunks.append(Chunk(f"{rel}#whole", rel, "record", text, 0, len(raw)))
        elif path.suffix == ".csv":
            rows = list(csv.reader(io.StringIO(text)))
            hdr = rows[0] if rows else []
            for i, row in enumerate(rows[1:], 1):
                frag = ", ".join(f"{h}={v}" for h, v in zip(hdr, row))
                chunks.append(Chunk(f"{rel}#{i}", rel, "record", frag, 0, len(raw)))
        elif path.suffix == ".xml":
            for i, line in enumerate(text.splitlines()):
                if line.strip():
                    chunks.append(Chunk(f"{rel}#{i}", rel, "record", line.strip(), 0, len(raw)))
        else:
            chunks.append(Chunk(f"{rel}#0", rel, "block", text, 0, len(raw)))
    return chunks


def _flatten_json(obj, prefix="") -> list[tuple[str, object]]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _flatten_json(v, f"{prefix}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _flatten_json(v, f"{prefix}/{i}")
    else:
        out.append((prefix, obj))
    return out
