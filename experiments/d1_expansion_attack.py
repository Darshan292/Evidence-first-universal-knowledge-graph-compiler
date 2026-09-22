"""Item 14: re-run the D-1 graph explosion attack under the ADR-0007 policy.

Verifies: edge-type filtering, hub damping, top-K bounding, explicit truncation,
total_reachable disclosure, and that no result silently claims completeness.
"""
from __future__ import annotations
import json, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.retrieval.chunker import build as build_chunks
from experiments.retrieval.retrievers import Hit, Index
from kgc.pipeline import ingest
from kgc.store import Store


def main():
    db = tempfile.mktemp(suffix=".db")
    store = Store(db)
    ingest(store, ROOT)                      # the whole repo: a real, hub-bearing graph
    chunks = build_chunks(ROOT, store)
    index = Index(chunks, store)

    deg = store.con.execute("""
        SELECT s.qualified_name qn, count(*) c FROM claim cl
          JOIN symbol s ON s.symbol_id = cl.object_id
         WHERE cl.predicate='CALLS' GROUP BY 1 ORDER BY c DESC LIMIT 5""").fetchall()
    print("highest in-degree symbols (the hubs):")
    for r in deg:
        print(f"   {r['qn']:46} in-degree {r['c']}")
    hub = deg[0]["qn"] if deg else None

    ordinary = store.con.execute("""
        SELECT s.qualified_name qn FROM symbol s WHERE s.kind='function'
          AND (SELECT count(*) FROM claim c WHERE c.object_id=s.symbol_id) BETWEEN 1 AND 3
        ORDER BY s.qualified_name LIMIT 1""").fetchone()
    ord_qn = ordinary["qn"] if ordinary else None

    def seed_for(qn):
        for cid, c in index.chunks.items():
            if c.symbol_qname == qn:
                return [Hit(cid, c.rel_path, 1.0, c.text, "seed")]
        return []

    rows = []
    matrix = [("ordinary", ord_qn), ("hub", hub)]
    for label, qn in matrix:
        if not qn:
            continue
        for depth in (1, 2, 3, 4):
            for kinds, kname in (( ("CALLS",), "CALLS-only"),
                                 (("CALLS", "CONTAINS", "IMPORTS"), "all-kinds")):
                t0 = time.perf_counter()
                hits, meta = index.expand(seed_for(qn), k=10, edge_kinds=kinds, max_depth=depth)
                ms = (time.perf_counter() - t0) * 1000
                rows.append({"node": label, "qname": qn, "depth": depth, "edge_kinds": kname,
                             "returned": len(hits), "total_reachable": meta["total_reachable"],
                             "truncated": meta["truncated"], "ms": round(ms, 1)})

    print(f"\n{'node':9} {'depth':6} {'edge kinds':12} {'returned':9} {'reachable':10} {'truncated':10} {'ms':>7}")
    for r in rows:
        print(f"{r['node']:9} {r['depth']:<6} {r['edge_kinds']:12} {r['returned']:<9} "
              f"{r['total_reachable']:<10} {str(r['truncated']):10} {r['ms']:>7}")

    checks = {
        "never_returns_more_than_k": all(r["returned"] <= 10 for r in rows),
        "total_reachable_always_disclosed": all("total_reachable" in r for r in rows),
        "truncation_flagged_whenever_reachable_exceeds_k":
            all(r["truncated"] == (r["total_reachable"] > 10) for r in rows),
        "edge_filtering_changes_reachable_set":
            any(a["total_reachable"] != b["total_reachable"]
                for a in rows for b in rows
                if a["node"] == b["node"] and a["depth"] == b["depth"]
                and a["edge_kinds"] != b["edge_kinds"]),
        "hub_expansion_bounded": all(r["returned"] <= 10 for r in rows if r["node"] == "hub"),
        "latency_p_max_ms": max(r["ms"] for r in rows),
    }
    print("\npolicy checks:")
    for k, v in checks.items():
        print(f"   {k:52} {v}")
    (ROOT / "experiments/d1_results.json").write_text(
        json.dumps({"rows": rows, "checks": checks}, indent=2))
    store.close()


if __name__ == "__main__":
    main()
