"""Gate 2.25 scaled claim-first evaluation on corpus3 (224 artifacts)."""
from __future__ import annotations
import json, statistics, sys, tempfile, time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.retrieval.claimfirst import (ABSTAIN, ABSTAIN_AMBIGUOUS, EXPOSE,
                                              EXPOSE_CONFLICTED, ClaimIndex, decide)
from experiments.retrieval.constraints import ParseStatus, extract
from kgc.pipeline import ingest
from kgc.store import Store

GOLD = json.loads((ROOT / "eval/GATE225_GOLD.json").read_text())
CORPUS = ROOT / GOLD["corpus_root"]
ABSTAINS = {ABSTAIN, ABSTAIN_AMBIGUOUS}


def main():
    db = tempfile.mktemp(suffix=".db")
    t0 = time.perf_counter()
    store = Store(db)
    rep = ingest(store, CORPUS)
    ingest_s = time.perf_counter() - t0
    index = ClaimIndex(store)

    counts = store.counts()
    rows, lat = [], []
    for q in GOLD["queries"]:
        t = time.perf_counter()
        d = decide(index, q["query"])
        lat.append((time.perf_counter() - t) * 1000)
        gold_units = set(q.get("answer_units", []))
        need = q.get("evidence_must_contain")
        tgt = q.get("claim_target") or {}

        claim_rank = evid_rank = None
        for i, h in enumerate(d.hits):
            want_ent = str(tgt.get("entity", "")).split(".")[-1].lower()
            ok_ent = want_ent and want_ent in h.subject_qname.lower()
            ok_rel = (not tgt.get("relation")) or h.predicate == tgt["relation"]
            want_obj = str(tgt.get("object", "")).split(".")[-1].lower()
            ok_obj = (not want_obj) or want_obj in ((h.object_qname or h.object_literal or "").lower())
            if ok_ent and ok_rel and ok_obj and claim_rank is None:
                claim_rank = i + 1
            if need and h.rel_path in gold_units and need in h.evidence_text and evid_rank is None:
                evid_rank = i + 1

        rows.append({
            "id": q["id"], "class": q["class"], "scope": q["scope"], "split": q["split"],
            "decision": d.outcome, "correct": d.outcome in q["expected_decision"],
            "failed_invariant": d.failed_invariant,
            "parse_status": extract(q["query"]).parse_status.value,
            "claim_rank": claim_rank, "evidence_rank": evid_rank,
            "exposed": d.outcome not in ABSTAINS,
            "n_hits": len(d.hits),
            "all_trusted": all(h.establishment in ("DERIVED", "CONFIRMED") for h in d.hits),
            "all_verified": all(h.evidence_strength in ("EXACT", "REPRODUCIBLE") for h in d.hits),
        })

    def at(rs, key, k):
        return round(sum(1 for r in rs if r[key] and r[key] <= k) / max(len(rs), 1), 3)

    def mrr(rs, key):
        return round(statistics.mean([1 / r[key] if r[key] else 0 for r in rs]) if rs else 0, 3)

    out = {"corpus": GOLD["corpus"], "artifacts": rep.seen,
           "ingest_seconds": round(ingest_s, 1), "db_counts": counts,
           "query_latency_ms_p50": round(statistics.median(lat), 2),
           "query_latency_ms_p95": round(sorted(lat)[max(0, int(0.95 * len(lat)) - 1)], 2)}

    for split in ("CALIBRATION", "VALIDATION", "TEST", "ALL"):
        rs = rows if split == "ALL" else [r for r in rows if r["split"] == split]
        ans = [r for r in rs if r["scope"] == "IN_SCOPE_ANSWERABLE"]
        uns = [r for r in rs if r["scope"] == "IN_SCOPE_UNSUPPORTED"]
        oos = [r for r in rs if r["scope"] == "OUT_OF_DETERMINISTIC_SCOPE"]
        by_cls = defaultdict(lambda: [0, 0])
        for r in uns:
            by_cls[r["class"]][1] += 1
            if not r["correct"]:
                by_cls[r["class"]][0] += 1
        amb = [r for r in rs if r["class"] == "ambiguous_entity"]
        out[split] = {
            "safety": {
                "n_unsupported": len(uns),
                "false_support_rate": round(sum(1 for r in uns if r["exposed"]) / max(len(uns), 1), 3),
                "unsupported_exposure": sum(1 for r in rs if r["exposed"] and not r["all_verified"]),
                "untrusted_exposure": sum(1 for r in rs if r["exposed"] and not r["all_trusted"]),
                "by_negative_class": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_cls.items())},
            },
            "availability": {
                "n_answerable": len(ans),
                "false_abstention_rate": round(sum(1 for r in ans if not r["exposed"]) / max(len(ans), 1), 3),
                "acceptance_rate": round(sum(1 for r in ans if r["exposed"]) / max(len(ans), 1), 3),
            },
            "retrieval": {
                "claim_recall@1": at(ans, "claim_rank", 1), "claim_recall@5": at(ans, "claim_rank", 5),
                "claim_recall@10": at(ans, "claim_rank", 10), "claim_MRR": mrr(ans, "claim_rank"),
                "evidence_recall@1": at(ans, "evidence_rank", 1),
                "evidence_recall@10": at(ans, "evidence_rank", 10),
                "evidence_MRR": mrr(ans, "evidence_rank"),
            },
            "ambiguity": {
                "n": len(amb),
                "ambiguity_abstention_rate": round(
                    sum(1 for r in amb if r["decision"] == ABSTAIN_AMBIGUOUS) / max(len(amb), 1), 3),
                "false_disambiguation_rate": round(
                    sum(1 for r in amb if r["exposed"]) / max(len(amb), 1), 3),
            },
            "out_of_scope": {
                "n": len(oos),
                "correctly_abstained": round(
                    sum(1 for r in oos if not r["exposed"]) / max(len(oos), 1), 3)},
        }
    out["parse_status_distribution"] = dict(Counter(r["parse_status"] for r in rows))
    out["parse_rate_answerable"] = round(
        sum(1 for r in rows if r["scope"] == "IN_SCOPE_ANSWERABLE"
            and r["parse_status"] == "PARSED") /
        max(sum(1 for r in rows if r["scope"] == "IN_SCOPE_ANSWERABLE"), 1), 3)

    print(f"corpus: {rep.seen} artifacts, ingest {ingest_s:.1f}s")
    print(f"graph : {counts['symbol']:,} symbols, {counts['claim']:,} claims, "
          f"{counts['evidence']:,} evidence")
    print(f"query : p50 {out['query_latency_ms_p50']}ms  p95 {out['query_latency_ms_p95']}ms\n")
    hdr = f"{'split':12} {'falseSup':9} {'untrust':8} {'unsupExp':9} {'falseAbs':9} {'claimR@10':10} {'evR@10':8} {'ambAbs':7}"
    print(hdr); print("-" * len(hdr))
    for split in ("CALIBRATION", "VALIDATION", "TEST", "ALL"):
        b = out[split]
        print(f"{split:12} {b['safety']['false_support_rate']:<9} "
              f"{b['safety']['untrusted_exposure']:<8} {b['safety']['unsupported_exposure']:<9} "
              f"{b['availability']['false_abstention_rate']:<9} "
              f"{b['retrieval']['claim_recall@10']:<10} {b['retrieval']['evidence_recall@10']:<8} "
              f"{b['ambiguity']['ambiguity_abstention_rate']:<7}")
    print(f"\nTEST false support by negative class: {out['TEST']['safety']['by_negative_class']}")
    print(f"out-of-scope correctly abstained (ALL): {out['ALL']['out_of_scope']['correctly_abstained']}")
    print(f"parse rate on answerable: {out['parse_rate_answerable']}")
    print(f"parse status distribution: {out['parse_status_distribution']}")
    (ROOT / "experiments/gate225_results.json").write_text(
        json.dumps({"summary": out, "rows": rows}, indent=2))
    store.close()


if __name__ == "__main__":
    main()
