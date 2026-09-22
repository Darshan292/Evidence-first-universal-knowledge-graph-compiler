"""S-D hybrid (chunk retrieval + claim support) and graph value by query class."""
from __future__ import annotations
import json, statistics, sys, tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.retrieval.chunker import build as build_chunks
from experiments.retrieval.claimfirst import ABSTAIN, EXPOSE, ClaimIndex, decide
from experiments.retrieval.retrievers import Index
from kgc.pipeline import ingest
from kgc.store import Store

GOLD = json.loads((ROOT / "eval/GATE2_GOLD.json").read_text())
CORPUS = ROOT / GOLD["corpus_root"]
STRUCTURAL = {"structural_relation", "cross_file", "exact_identifier"}


def main():
    db = tempfile.mktemp(suffix=".db")
    store = Store(db); ingest(store, CORPUS)
    chunks = build_chunks(CORPUS, store)
    index = Index(chunks, store)
    cindex = ClaimIndex(store)

    rows = []
    for q in GOLD["queries"]:
        gold = set(q.get("answer_units", []))
        need = q.get("evidence_must_contain")
        outcome, reason, claim_hits, cons = decide(cindex, q["query"])

        lex = index.r1(q["query"])
        lex_graph, _ = index.r2(q["query"])

        def rank(hits):
            return next((i + 1 for i, h in enumerate(hits) if h.rel_path in gold), None)

        # S-D hybrid: claim-first OWNS the support decision; chunk retrieval
        # supplies documents only when the claim layer has already exposed.
        hy_decision = outcome
        hy_rank = rank(lex) if outcome != ABSTAIN else None
        hy_evidence = bool(need) and outcome != ABSTAIN and (
            any(h.rel_path in gold and need in h.evidence_text for h in claim_hits)
            or any(h.rel_path in gold and need in h.text for h in lex))

        rows.append({
            "id": q["id"], "class": q["class"], "answerable": q["answerable"],
            "expected": q["expected_decision"], "split": q["split"],
            "structural": q["class"] in STRUCTURAL,
            "lex_rank": rank(lex), "lexgraph_rank": rank(lex_graph),
            "claim_decision": outcome,
            "hybrid_decision": hy_decision,
            "hybrid_correct": hy_decision in q["expected_decision"],
            "hybrid_rank": hy_rank, "hybrid_evidence": hy_evidence,
        })

    pos = [r for r in rows if r["answerable"]]
    neg = [r for r in rows if not r["answerable"]]

    def mrr(rs, key):
        return round(statistics.mean([1 / r[key] if r[key] else 0 for r in rs]), 3) if rs else 0

    struct = [r for r in pos if r["structural"]]
    semantic = [r for r in pos if not r["structural"]]

    out = {
        "hybrid_S_D": {
            "false_support_rate": round(sum(1 for r in neg if not r["hybrid_correct"]) / len(neg), 3),
            "false_abstention_rate": round(
                sum(1 for r in pos if r["hybrid_decision"] == ABSTAIN) / len(pos), 3),
            "doc_recall@10_when_exposed": round(
                sum(1 for r in pos if r["hybrid_rank"]) / max(
                    sum(1 for r in pos if r["hybrid_decision"] != ABSTAIN), 1), 3),
            "evidence_correct": round(sum(1 for r in pos if r["hybrid_evidence"]) / len(pos), 3),
        },
        "graph_value_by_class": {
            "structural": {"n": len(struct), "lex_MRR": mrr(struct, "lex_rank"),
                           "lex_graph_MRR": mrr(struct, "lexgraph_rank"),
                           "delta": round(mrr(struct, "lexgraph_rank") - mrr(struct, "lex_rank"), 3)},
            "semantic": {"n": len(semantic), "lex_MRR": mrr(semantic, "lex_rank"),
                         "lex_graph_MRR": mrr(semantic, "lexgraph_rank"),
                         "delta": round(mrr(semantic, "lexgraph_rank") - mrr(semantic, "lex_rank"), 3)},
        },
    }
    print(json.dumps(out, indent=2))
    (ROOT / "experiments/hybrid_graph_results.json").write_text(
        json.dumps({"summary": out, "rows": rows}, indent=2))
    store.close()


if __name__ == "__main__":
    main()
