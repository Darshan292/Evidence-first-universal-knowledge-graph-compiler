"""X1-RERUN-01: paraphrase retrieval with mechanically verified vocabulary overlap.

ADDITIVE. The original X-1 result file is not read or modified.
"""
from __future__ import annotations
import json, statistics, sys, tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.retrieval.chunker import build as build_chunks
from experiments.retrieval.retrievers import Index
from kgc.pipeline import ingest
from kgc.store import Store

GOLD = json.loads((ROOT / "eval/x1rerun/X1_RERUN_01_GOLD.json").read_text())
CORPUS = ROOT / GOLD["corpus_root"]


def main():
    db = tempfile.mktemp(suffix=".db")
    store = Store(db); ingest(store, CORPUS)
    chunks = build_chunks(CORPUS, store)
    index = Index(chunks, store)
    index._build_lsa()

    runners = {"R1_bm25": index.r1, "R3_lsa": index.r3,
               "R4_bm25+lsa": index.r4,
               "R2_bm25+graph": lambda q, k=10: index.r2(q, k)[0]}

    out = defaultdict(dict)
    rows = []
    for q in GOLD["accepted"]:
        gold = set(q["answer_units"])
        rec = {"id": q["id"], "tier": q["tier"], "overlap": q["overlap_fraction"]}
        for name, fn in runners.items():
            hits = fn(q["query"])
            paths = [h.rel_path for h in hits]
            rank = next((i + 1 for i, p in enumerate(paths) if p in gold), None)
            rec[name] = {"rank": rank, "n_hits": len(hits), "top3": paths[:3]}
        rows.append(rec)

    summary = {}
    for name in runners:
        for tier in ("zero_overlap", "partial_overlap", "full_overlap"):
            rs = [r for r in rows if r["tier"] == tier]
            found = [r for r in rs if r[name]["rank"]]
            summary.setdefault(name, {})[tier] = {
                "n": len(rs),
                "recall@10": round(len(found) / max(len(rs), 1), 3),
                "recall@1": round(sum(1 for r in rs if r[name]["rank"] == 1) / max(len(rs), 1), 3),
                "MRR": round(statistics.mean([1 / r[name]["rank"] if r[name]["rank"] else 0
                                              for r in rs]), 3),
                "empty_result_sets": sum(1 for r in rs if r[name]["n_hits"] == 0),
            }

    print(f"{'retriever':16} {'tier':17} {'n':>3} {'R@10':>6} {'R@1':>6} {'MRR':>6} {'empty':>6}")
    print("-" * 66)
    for name in runners:
        for tier, s in summary[name].items():
            print(f"{name:16} {tier:17} {s['n']:>3} {s['recall@10']:>6} {s['recall@1']:>6} "
                  f"{s['MRR']:>6} {s['empty_result_sets']:>6}")
        print()

    (ROOT / "experiments/x1_rerun_01_results.json").write_text(
        json.dumps({"summary": summary, "rows": rows,
                    "note": "LSA vocabulary is corpus-derived; a zero-overlap query "
                            "projects to the zero vector and returns nothing. This is "
                            "structural, not a tuning failure."}, indent=2))
    store.close()


if __name__ == "__main__":
    main()
