"""Gate 2 core experiment: claim-first vs chunk-first support.

Measures both on the same independently-authored corpus and gold set.
"""
from __future__ import annotations
import json, statistics, sys, tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.retrieval.chunker import build as build_chunks
from experiments.retrieval.claimfirst import ABSTAIN, ClaimIndex, decide
from experiments.retrieval.constraints import extract
from experiments.retrieval.retrievers import Index
from experiments.retrieval.support import SupportGate
from kgc.pipeline import ingest
from kgc.store import Store

GOLD = json.loads((ROOT / "eval/GATE2_GOLD.json").read_text())
CORPUS = ROOT / GOLD["corpus_root"]


def localize_in_file(chunks_by_path, rel_path, needle):
    """Within-file evidence localization (the Gate 1.75 minimum correction):
    once a file is retrieved, scan ITS chunks for the evidence rather than
    requiring the evidence chunk to win the global ranking."""
    for c in chunks_by_path.get(rel_path, []):
        if needle in c.text:
            return c
    return None


def main():
    db = tempfile.mktemp(suffix=".db")
    store = Store(db); ingest(store, CORPUS)
    chunks = build_chunks(CORPUS, store)
    index = Index(chunks, store)
    cindex = ClaimIndex(store)
    gate = SupportGate(chunks, store, design="G2", idf_percentile=90)
    by_path = defaultdict(list)
    for c in chunks:
        by_path[c.rel_path].append(c)

    rows = []
    for q in GOLD["queries"]:
        gold_units = set(q.get("answer_units", []))
        need = q.get("evidence_must_contain")
        rec = {"id": q["id"], "kind": q["kind"], "class": q["class"], "split": q["split"],
               "answerable": q["answerable"], "expected": q["expected_decision"]}

        # ---- chunk-first (Gate 1.75 architecture) ----
        hits = index.r1(q["query"])
        paths = [h.rel_path for h in hits]
        d = gate.decide(q["query"], hits)
        rec["chunk_first"] = {
            "decision": d.outcome,
            "correct": d.outcome in q["expected_decision"],
            "doc_rank": next((i + 1 for i, p in enumerate(paths) if p in gold_units), None),
            "evidence_ok": bool(need) and any(
                h.rel_path in gold_units and need in h.text for h in hits),
        }
        # within-file localization applied to chunk-first
        loc = None
        if need:
            for p in paths:
                if p in gold_units:
                    loc = localize_in_file(by_path, p, need)
                    if loc:
                        break
        rec["chunk_first_localized_evidence_ok"] = bool(loc)

        # ---- claim-first ----
        _d = decide(cindex, q["query"])
        outcome, reason, claim_hits, cons = (_d.outcome, _d.reason, _d.hits,
                                             _d.constraints)
        c_paths = [h.rel_path for h in claim_hits]
        claim_rank = None
        tgt = q.get("claim_target") or {}
        for i, h in enumerate(claim_hits):
            subj = h.subject_qname.split(".")[-1].lower()
            want_ent = str(tgt.get("entity", "")).split(".")[-1].lower()
            want_obj = str(tgt.get("object", "")).split(".")[-1].lower()
            ok_ent = want_ent and (want_ent in h.subject_qname.lower() or subj == want_ent)
            ok_rel = (not tgt.get("relation")) or h.predicate == tgt["relation"]
            ok_obj = (not want_obj) or want_obj in ((h.object_qname or h.object_literal or "").lower())
            if ok_ent and ok_rel and ok_obj:
                claim_rank = i + 1
                break
        rec["claim_first"] = {
            "decision": outcome,
            "correct": outcome in q["expected_decision"],
            "parsed": cons.parsed if cons else False,
            "shape": cons.shape if cons else "n/a", "reason": reason[:70],
            "claim_rank": claim_rank,
            "doc_rank": next((i + 1 for i, p in enumerate(c_paths) if p in gold_units), None),
            "evidence_ok": bool(need) and any(
                h.rel_path in gold_units and need in h.evidence_text for h in claim_hits),
            "n_claims": len(claim_hits),
        }
        rows.append(rec)

    def agg(rs, arch, key):
        vals = [r[arch][key] for r in rs]
        return round(sum(1 for v in vals if v) / max(len(vals), 1), 3)

    out = {"corpus": GOLD["corpus"], "n": len(rows)}
    for split in ("CALIBRATION", "VALIDATION", "TEST", "ALL"):
        rs = rows if split == "ALL" else [r for r in rows if r["split"] == split]
        pos = [r for r in rs if r["answerable"]]
        neg = [r for r in rs if not r["answerable"]]
        blk = {}
        for arch in ("chunk_first", "claim_first"):
            fs = sum(1 for r in neg if not r[arch]["correct"]) / max(len(neg), 1)
            fa = sum(1 for r in pos if r[arch]["decision"] == ABSTAIN) / max(len(pos), 1)
            blk[arch] = {
                "n_pos": len(pos), "n_neg": len(neg),
                "false_support_rate": round(fs, 3),
                "false_abstention_rate": round(fa, 3),
                "doc_recall@10": agg(pos, arch, "doc_rank"),
                "evidence_correct": agg(pos, arch, "evidence_ok"),
            }
            if arch == "claim_first":
                blk[arch]["claim_recall@10"] = agg(pos, arch, "claim_rank")
                blk[arch]["claim_MRR"] = round(statistics.mean(
                    [1 / r["claim_first"]["claim_rank"] if r["claim_first"]["claim_rank"] else 0
                     for r in pos]) if pos else 0, 3)
                blk[arch]["constraint_parse_rate"] = agg(pos, arch, "parsed")
        blk["chunk_first"]["evidence_correct_with_localization"] = round(
            sum(1 for r in pos if r["chunk_first_localized_evidence_ok"]) / max(len(pos), 1), 3)
        out[split] = blk

    print(f"corpus: {GOLD['corpus']}\nqueries: {len(rows)}\n")
    hdr = f"{'split':12} {'arch':12} {'falseSup':9} {'falseAbs':9} {'docR@10':8} {'evidence':9} {'claimR@10':10}"
    print(hdr); print("-" * len(hdr))
    for split in ("CALIBRATION", "VALIDATION", "TEST", "ALL"):
        for arch in ("chunk_first", "claim_first"):
            b = out[split][arch]
            print(f"{split:12} {arch:12} {b['false_support_rate']:<9} {b['false_abstention_rate']:<9} "
                  f"{b['doc_recall@10']:<8} {b['evidence_correct']:<9} "
                  f"{b.get('claim_recall@10','-'):<10}")
        print()
    a = out["ALL"]
    print(f"within-file localization lifts chunk-first evidence "
          f"{a['chunk_first']['evidence_correct']} -> "
          f"{a['chunk_first']['evidence_correct_with_localization']}")
    print(f"constraint parse rate on answerable queries: "
          f"{a['claim_first']['constraint_parse_rate']}")
    (ROOT / "experiments/claim_first_results.json").write_text(
        json.dumps({"summary": out, "rows": rows}, indent=2))
    store.close()


if __name__ == "__main__":
    main()
