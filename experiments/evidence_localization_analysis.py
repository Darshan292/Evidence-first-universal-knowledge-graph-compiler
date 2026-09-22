"""Item 15: diagnose the ~14.3% evidence-localization gap. Diagnosis only --
nothing is changed until the cause is known.

Failure taxonomy:
  WRONG_FILE          no gold file retrieved at all
  RIGHT_FILE_NO_CHUNK gold file retrieved, but no returned chunk holds the evidence
  CHUNK_TOO_NARROW    the evidence exists in the file, split across chunk boundaries
  CHUNK_NOT_RETRIEVED the chunk holding the evidence exists but ranked outside top-k
  GOLD_STRING_ISSUE   the required string is not literally in the gold file
"""
from __future__ import annotations
import json, sys, tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.retrieval.chunker import build as build_chunks
from experiments.retrieval.retrievers import Index
from kgc.pipeline import ingest
from kgc.store import Store

GOLD = json.loads((ROOT / "eval/EVALUATION_GOLD.json").read_text())
CORPUS = ROOT / GOLD["corpus_root"]


def main():
    db = tempfile.mktemp(suffix=".db")
    store = Store(db); ingest(store, CORPUS)
    chunks = build_chunks(CORPUS, store)
    index = Index(chunks, store)
    by_path = {}
    for c in chunks:
        by_path.setdefault(c.rel_path, []).append(c)

    findings = []
    for q in GOLD["queries"]:
        if not q["answerable"]:
            continue
        need = q.get("evidence_must_contain")
        if not need:
            continue
        gold = set(q["answer_units"])
        hits = index.r1(q["query"])
        paths = [h.rel_path for h in hits]

        file_hit = any(p in gold for p in paths)
        ev_hit = any(h.rel_path in gold and need in h.text for h in hits)
        if ev_hit:
            continue                                  # evidence correctly localized

        in_source = any(need in (CORPUS / u).read_text(encoding="utf-8") for u in gold)
        chunk_holding = [c for u in gold for c in by_path.get(u, []) if need in c.text]

        if not in_source:
            cat = "GOLD_STRING_ISSUE"
        elif not file_hit:
            cat = "WRONG_FILE"
        elif not chunk_holding:
            cat = "CHUNK_TOO_NARROW"          # in the file, but no single chunk holds it
        else:
            retrieved_ids = {h.chunk_id for h in hits}
            cat = ("CHUNK_NOT_RETRIEVED"
                   if not any(c.chunk_id in retrieved_ids for c in chunk_holding)
                   else "RIGHT_FILE_NO_CHUNK")
        findings.append({"id": q["id"], "class": q["class"], "category": cat,
                         "need": need[:46], "gold": sorted(gold),
                         "retrieved_top3": paths[:3],
                         "chunks_holding_evidence": [c.chunk_id for c in chunk_holding][:3]})

    n_ans = sum(1 for q in GOLD["queries"] if q["answerable"] and q.get("evidence_must_contain"))
    print(f"queries with an evidence requirement: {n_ans}")
    print(f"localization failures: {len(findings)}  ({len(findings)/max(n_ans,1):.1%})\n")
    print("cause breakdown:")
    for cat, n in Counter(f["category"] for f in findings).most_common():
        print(f"   {cat:22} {n}")
    print()
    for f in findings:
        print(f"  {f['id']:7} {f['category']:22} need={f['need']!r}")
        print(f"          gold={f['gold']}  top3={f['retrieved_top3']}")
        print(f"          chunks holding the evidence: {f['chunks_holding_evidence'] or 'NONE'}")
    (ROOT / "experiments/evidence_localization_results.json").write_text(
        json.dumps({"n_queries": n_ans, "failures": findings,
                    "breakdown": dict(Counter(f["category"] for f in findings))}, indent=2))
    store.close()


if __name__ == "__main__":
    main()
