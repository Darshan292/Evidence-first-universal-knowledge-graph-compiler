"""J-1 harness: run the frozen independent query set against a fresh database.

Measurement only. It imports the system under test and changes nothing in it.
Evidence is re-verified against the ORIGINAL corpus files on disk, not against
the database's own locator arithmetic.

    python3 experiments/j1_eval.py --run 1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.retrieval.claimfirst import ClaimIndex, decide
from experiments.retrieval.constraints import extract
from kgc.pipeline import ingest
from kgc.store import Store

CORPUS = ROOT / "eval/corpus3"
QUERIES = ROOT / "eval/J1_INDEPENDENT_QUERIES.json"
GOLD = ROOT / "eval/J1_GOLD.json"
TRUSTED = {"DERIVED", "CONFIRMED"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(db: Path) -> Store:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(str(db))
    rep = ingest(store, CORPUS.resolve())
    assert rep.absent == [], f"incomplete substrate: {rep.absent}"
    assert store.check_invariants() == [], store.check_invariants()
    return store


def check_evidence(hit) -> dict:
    """Re-read the cited bytes from the original file. No DB arithmetic."""
    path = CORPUS / hit["rel_path"]
    out = {"rel_path": hit["rel_path"], "localized": False, "exact": False,
           "byte_start": hit["byte_start"], "byte_end": hit["byte_end"]}
    if not path.is_file():
        out["error"] = "cited artifact is not a file in the corpus"
        return out
    data = path.read_bytes()
    s, e = hit["byte_start"], hit["byte_end"]
    if not (0 <= s <= e <= len(data)):
        out["error"] = f"byte range [{s},{e}] outside a {len(data)}-byte file"
        return out
    out["localized"] = True
    try:
        actual = data[s:e].decode("utf-8")
    except UnicodeDecodeError as exc:
        out["error"] = f"cited bytes are not UTF-8: {exc}"
        return out
    out["exact"] = actual == hit["evidence_text"]
    out["source_bytes"] = actual
    if not out["exact"]:
        out["error"] = "cited text differs from the source at that range"
    return out


def run(db: Path) -> dict:
    queries = json.loads(QUERIES.read_text())["queries"]
    store = build(db)
    index = ClaimIndex(store)
    rows = []
    for q in queries:
        c = extract(q["question"])
        d = decide(index, q["question"])
        hits = [{
            "claim_id": h.claim_id, "predicate": h.predicate,
            "subject": h.subject_qname, "object": h.object_qname,
            "object_literal": h.object_literal, "rel_path": h.rel_path,
            "evidence_text": h.evidence_text, "strength": h.evidence_strength,
            "establishment": h.establishment, "lifecycle": h.lifecycle,
        } for h in d.hits]
        # locators are not on ClaimHit; fetch them for the independent check
        for h in hits:
            r = store.con.execute(
                "SELECT e.locator FROM claim_evidence ce JOIN evidence e"
                "  USING(evidence_id) WHERE ce.claim_id=? LIMIT 1", (h["claim_id"],)).fetchone()
            loc = json.loads(r["locator"]) if r else {}
            h["byte_start"] = loc.get("byte_start", -1)
            h["byte_end"] = loc.get("byte_end", -1)
            h["line_start"] = loc.get("line_start")
        rows.append({
            "id": q["id"], "question": q["question"],
            "parse_status": c.parse_status.value, "shape": c.shape,
            "subject": c.subject, "predicate": c.predicate, "prop_word": c.prop_word,
            "obj": c.obj, "source_scope": c.source_scope, "literal": c.literal,
            "decision": d.outcome, "failed_invariant": d.failed_invariant,
            "reason": d.reason, "n_hits": len(hits),
            "conflict": d.conflict, "hits": hits,
            "evidence_checks": [check_evidence(h) for h in hits],
            "all_trusted": all(h["establishment"] in TRUSTED for h in hits) if hits else None,
        })
    counts = store.counts()
    store.close()
    return {
        "queries_sha256": sha256(QUERIES),
        "gold_sha256": sha256(GOLD) if GOLD.exists() else None,
        "db_counts": counts,
        "n_queries": len(queries),
        "rows": rows,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=1)
    a = ap.parse_args()
    out = run(ROOT / f"eval/j1/j1_run{a.run}.sqlite")
    dest = ROOT / f"experiments/j1_raw_run{a.run}.json"
    dest.write_text(json.dumps(out, indent=2))

    from collections import Counter
    ps = Counter(r["parse_status"] for r in out["rows"])
    dc = Counter(r["decision"] for r in out["rows"])
    print(f"run {a.run}: {out['n_queries']} queries  queries_sha={out['queries_sha256'][:16]}")
    print(f"  parse status : {dict(ps)}")
    print(f"  decisions    : {dict(dc)}")
    print(f"  wrote {dest.relative_to(ROOT)}")
