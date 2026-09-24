"""Graph reads for the workbench: statistics, symbol search, neighbourhoods.

Every edge carries the claim and evidence that produced it. The picture is not a
diagram drawn beside the data -- it *is* the data, and clicking an edge returns
the claim id, the establishment, and the exact source span behind it.

Nothing here renders the whole graph. A neighbourhood starts from one symbol and
expands by hops, because 6,102 symbols on one canvas is a screensaver.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

EDGE_PREDICATES = ("CONTAINS", "CALLS", "IMPORTS", "EXTENDS", "HAS_VALUE")
MAX_NODES = 220


class GraphReader:
    def __init__(self, db_path: str | Path):
        self.con = sqlite3.connect(str(db_path))
        self.con.row_factory = sqlite3.Row

    def close(self) -> None:
        self.con.close()

    # ── dashboard ───────────────────────────────────────────────────────
    def statistics(self) -> dict:
        q = lambda s, *a: self.con.execute(s, a).fetchone()[0]
        status = {r[0]: r[1] for r in self.con.execute(
            "SELECT parse_status, count(*) FROM artifact GROUP BY parse_status")}
        return {
            "artifacts": q("SELECT count(*) FROM artifact"),
            "symbols": q("SELECT count(*) FROM symbol"),
            "claims": q("SELECT count(*) FROM claim"),
            "evidence": q("SELECT count(*) FROM evidence"),
            "relationships": q("SELECT count(*) FROM claim WHERE predicate != 'CONTAINS'"),
            "diagnostics": q("SELECT count(*) FROM diagnostic"),
            "ok": status.get("OK", 0),
            "partial": status.get("PARTIAL", 0),
            "failed": status.get("FAILED", 0),
            "unsupported": status.get("UNSUPPORTED", 0),
            "skipped": status.get("SKIPPED", 0),
            "predicates": {r[0]: r[1] for r in self.con.execute(
                "SELECT predicate, count(*) FROM claim GROUP BY predicate ORDER BY 2 DESC")},
            "symbol_kinds": {r[0]: r[1] for r in self.con.execute(
                "SELECT kind, count(*) FROM symbol GROUP BY kind ORDER BY 2 DESC")},
            "resolution": {r[0]: r[1] for r in self.con.execute(
                "SELECT resolution, count(*) FROM reference GROUP BY resolution")},
        }

    def problem_artifacts(self, limit: int = 100) -> list[dict]:
        """Failed and unsupported files, with their reason. Never hidden."""
        return [dict(r) for r in self.con.execute(
            "SELECT rel_path, parse_status, parse_error, size_bytes, sha256"
            "  FROM artifact WHERE parse_status != 'OK'"
            " ORDER BY parse_status, rel_path LIMIT ?", (limit,))]

    # ── search ──────────────────────────────────────────────────────────
    def search_symbols(self, term: str, limit: int = 40) -> list[dict]:
        if not term.strip():
            return []
        like = f"%{term.strip()}%"
        rows = self.con.execute(
            "SELECT s.symbol_id, s.qualified_name, s.name, s.kind, s.locator,"
            "       s.locator_kind, a.rel_path"
            "  FROM symbol s JOIN artifact a USING(artifact_id)"
            " WHERE s.qualified_name LIKE ? OR s.name LIKE ?"
            " ORDER BY (s.name = ?) DESC, length(s.qualified_name), s.qualified_name"
            " LIMIT ?", (like, like, term.strip(), limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # a qualified name is a NAME, not an identity (K-1.1): two rows can
            # share one, so the picker shows where each occurrence lives
            d["location"] = describe_location(d["locator_kind"], json.loads(d.pop("locator")))
            out.append(d)
        return out

    # ── node detail ─────────────────────────────────────────────────────
    def node(self, symbol_id: str) -> dict | None:
        r = self.con.execute(
            "SELECT s.symbol_id, s.qualified_name, s.name, s.kind, s.docstring,"
            "       s.locator, s.locator_kind, s.parent_id, a.rel_path, a.parse_status"
            "  FROM symbol s JOIN artifact a USING(artifact_id)"
            " WHERE s.symbol_id = ?", (symbol_id,)).fetchone()
        if r is None:
            return None
        out = dict(r)
        out["locator"] = json.loads(out["locator"])
        out["neighbours"] = self._degree(symbol_id)
        return out

    def _degree(self, symbol_id: str) -> dict:
        out = {}
        for r in self.con.execute(
            "SELECT predicate, count(*) n FROM claim"
            " WHERE subject_id = ? OR object_id = ? GROUP BY predicate", (symbol_id, symbol_id)):
            out[r["predicate"]] = r["n"]
        return out

    # ── neighbourhood ───────────────────────────────────────────────────
    def neighbourhood(self, symbol_id: str, hops: int = 1,
                      predicates: tuple[str, ...] = EDGE_PREDICATES,
                      limit: int = MAX_NODES) -> dict:
        preds = tuple(p for p in predicates if p in EDGE_PREDICATES) or EDGE_PREDICATES
        placeholders = ",".join("?" * len(preds))
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        seen_edges: set[str] = set()
        frontier = [symbol_id]
        truncated = False

        def add_node(sid: str, depth: int) -> None:
            if sid in nodes or sid is None:
                return
            n = self.node(sid)
            if n:
                n["depth"] = depth
                nodes[sid] = n

        add_node(symbol_id, 0)
        for depth in range(max(1, min(hops, 2))):
            nxt: list[str] = []
            for sid in frontier:
                rows = self.con.execute(
                    f"SELECT cl.claim_id, cl.predicate, cl.subject_id, cl.object_id,"
                    f"       cl.object_literal, cl.establishment,"
                    f"       s.qualified_name AS subj, o.qualified_name AS obj"
                    f"  FROM claim cl JOIN symbol s ON s.symbol_id = cl.subject_id"
                    f"  LEFT JOIN symbol o ON o.symbol_id = cl.object_id"
                    f" WHERE (cl.subject_id = ? OR cl.object_id = ?)"
                    f"   AND cl.predicate IN ({placeholders})"
                    f" ORDER BY cl.predicate, cl.claim_id", (sid, sid, *preds)).fetchall()
                for r in rows:
                    if len(nodes) >= limit:
                        truncated = True
                        break
                    if r["claim_id"] in seen_edges:
                        continue
                    seen_edges.add(r["claim_id"])
                    other = r["object_id"] if r["subject_id"] == sid else r["subject_id"]
                    if other:
                        add_node(other, depth + 1)
                        nxt.append(other)
                    edges.append({
                        "claim_id": r["claim_id"], "predicate": r["predicate"],
                        "source": r["subject_id"], "target": r["object_id"],
                        "subject": r["subj"], "object": r["obj"] or r["object_literal"],
                        "resolved": r["object_id"] is not None,
                        "establishment": r["establishment"]})
            frontier = nxt
            if truncated:
                break
        return {"root": symbol_id, "hops": hops, "predicates": list(preds),
                "truncated": truncated, "limit": limit,
                "nodes": list(nodes.values()), "edges": edges}

    # ── edge detail: the claim and its evidence ─────────────────────────
    def edge(self, claim_id: str) -> dict | None:
        r = self.con.execute(
            "SELECT cl.claim_id, cl.predicate, cl.object_literal, cl.establishment,"
            "       cl.lifecycle, cl.extractor_id, cl.extractor_version, cl.model_id,"
            "       s.qualified_name AS subj, s.symbol_id AS subject_id,"
            "       o.qualified_name AS obj, o.symbol_id AS object_id"
            "  FROM claim cl JOIN symbol s ON s.symbol_id = cl.subject_id"
            "  LEFT JOIN symbol o ON o.symbol_id = cl.object_id"
            " WHERE cl.claim_id = ?", (claim_id,)).fetchone()
        if r is None:
            return None
        out = dict(r)
        out["evidence"] = [self.evidence(x["evidence_id"]) for x in self.con.execute(
            "SELECT evidence_id FROM claim_evidence WHERE claim_id = ?", (claim_id,))]
        ref = self.con.execute(
            "SELECT to_name, resolution, reason FROM reference WHERE claim_id = ?",
            (claim_id,)).fetchone()
        out["resolution"] = dict(ref) if ref else None
        return out

    def evidence(self, evidence_id: str) -> dict | None:
        r = self.con.execute(
            "SELECT e.evidence_id, e.locator, e.locator_kind, e.quoted_text,"
            "       e.verification_strength, e.verifier_engine, e.artifact_sha256,"
            "       a.rel_path, a.media_type"
            "  FROM evidence e JOIN artifact a ON a.artifact_id = e.artifact_id"
            " WHERE e.evidence_id = ?", (evidence_id,)).fetchone()
        if r is None:
            return None
        out = dict(r)
        loc = json.loads(out["locator"])
        out["locator"] = loc
        out["location"] = describe_location(out["locator_kind"], loc)
        return out


def describe_location(locator_kind: str, loc: dict) -> str:
    """A reader-facing address. Never invents one the format cannot support."""
    if locator_kind == "byte_range":
        line = loc.get("line_start")
        span = f"bytes {loc.get('byte_start')}–{loc.get('byte_end')}"
        return f"line {line}, {span}" if line else span
    if locator_kind == "pdf_box":
        page = loc.get("page")
        return f"page {page}" if page else "whole document"
    if locator_kind == "docx_para":
        unit, idx = loc.get("unit", "paragraph"), loc.get("para")
        # DOCX carries no page numbers: pagination belongs to the renderer
        return "whole document" if idx is None or idx < 0 else f"{unit} {idx}"
    return locator_kind
