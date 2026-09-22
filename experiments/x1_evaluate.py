"""X-1 evaluator. Reads corpus + gold + retriever output. Changes none of them.

Scoring follows eval/PREREGISTRATION.md, which was committed before any
retrieval code existed.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.retrieval import retrievers
from experiments.retrieval.chunker import build as build_chunks
from experiments.retrieval.preprocess import tokenize
from kgc.pipeline import ingest
from kgc.store import Store

GOLD = json.loads((ROOT / "eval/EVALUATION_GOLD.json").read_text())
CORPUS = ROOT / GOLD["corpus_root"]
K_SET = (1, 5, 10)


def check_separation() -> list[str]:
    """The retrieval implementation must not read any gold file."""
    bad = []
    for f in (ROOT / "experiments/retrieval").rglob("*.py"):
        src = f.read_text()
        for marker in ("EVALUATION_GOLD", "CODE_ANALYSIS_GOLD", "eval/EVALUATION",
                       "answer_units", "evidence_must_contain"):
            if marker in src:
                bad.append(f"{f.name} references gold marker {marker!r}")
    for f in (ROOT / "experiments/retrieval").rglob("*.py"):
        src = f.read_text().lower()
        for banned in ("openai", "anthropic", "groq", "ollama", "llm", "gpt"):
            if re.search(rf"\b{banned}\b", src):
                bad.append(f"{f.name} references {banned!r} -- LLM use is prohibited in this gate")
    return bad


def run_config(index, name, query):
    meta = {}
    t0 = time.perf_counter()
    if name == "R0":
        hits = index.r0(query)
    elif name == "R1":
        hits = index.r1(query)
    elif name == "R2":
        hits, meta = index.r2(query)
    elif name == "R3":
        hits = index.r3(query)
    elif name == "R4":
        hits = index.r4(query)
    elif name == "R5":
        hits, meta = index.r5(query)
    else:
        raise ValueError(name)
    return hits, (time.perf_counter() - t0) * 1000, meta


def grounded(query: str, hits) -> bool:
    """Uniform abstention rule, identical for every configuration: a result is
    'supported' only if the top hit shares a content term with the query."""
    if not hits:
        return False
    qt = set(tokenize(query))
    return bool(qt & set(tokenize(hits[0].text)))


def evaluate(index, configs):
    per_cfg = {}
    for cfg in configs:
        rows, lat = [], []
        for q in GOLD["queries"]:
            hits, ms, meta = run_config(index, cfg, q["query"])
            lat.append(ms)
            paths = [h.rel_path for h in hits]
            gold_units = set(q["answer_units"])

            rank = next((i + 1 for i, p in enumerate(paths) if p in gold_units), None)
            ev_needed = q.get("evidence_must_contain")
            ev_rank = None
            if ev_needed:
                ev_rank = next((i + 1 for i, h in enumerate(hits)
                                if h.rel_path in gold_units and ev_needed in h.text), None)

            rows.append({
                "id": q["id"], "class": q["class"], "answerable": q["answerable"],
                "rank": rank, "evidence_rank": ev_rank,
                "grounded": grounded(q["query"], hits),
                "paths": paths[:10], "meta": meta,
                "conflict_ok": (all(any(p == c for p in paths) for c in q["conflict_pair"])
                                if q.get("conflict_pair") else None),
                "distractor_above_gold": (
                    _distractor_above(paths, gold_units, q.get("distractors", []))
                    if q.get("distractors") else None),
            })
        per_cfg[cfg] = {"rows": rows, "latency_ms_p50": round(statistics.median(lat), 2),
                        "latency_ms_p95": round(sorted(lat)[max(0, int(0.95 * len(lat)) - 1)], 2)}
    return per_cfg


def _distractor_above(paths, gold_units, distractors):
    g = next((i for i, p in enumerate(paths) if p in gold_units), 999)
    d = next((i for i, p in enumerate(paths) if p in distractors), 999)
    return d < g


def summarize(per_cfg):
    out = {}
    for cfg, data in per_cfg.items():
        rows = data["rows"]
        ans = [r for r in rows if r["answerable"]]
        neg = [r for r in rows if not r["answerable"]]

        def recall_at(rs, k):
            return round(sum(1 for r in rs if r["rank"] and r["rank"] <= k) / max(len(rs), 1), 3)

        by_class = defaultdict(list)
        for r in ans:
            by_class[r["class"]].append(r)

        ev_rows = [r for r in ans if r["evidence_rank"] is not None or r["rank"] is not None]
        ev_ok = sum(1 for r in ans if r["evidence_rank"] and r["evidence_rank"] <= 10)
        ans_ok = sum(1 for r in ans if r["rank"] and r["rank"] <= 10)

        out[cfg] = {
            "overall": {f"recall@{k}": recall_at(ans, k) for k in K_SET} | {
                "MRR": round(statistics.mean([1 / r["rank"] if r["rank"] else 0 for r in ans]), 3),
                "answer_correct@10": round(ans_ok / max(len(ans), 1), 3),
                "evidence_correct@10": round(ev_ok / max(len(ans), 1), 3),
                "evidence_gap": round((ans_ok - ev_ok) / max(len(ans), 1), 3),
            },
            "per_class": {c: {f"recall@{k}": recall_at(rs, k) for k in K_SET}
                          for c, rs in sorted(by_class.items())},
            "negative": {
                "n": len(neg),
                "false_support_rate": round(
                    sum(1 for r in neg if r["grounded"]) / max(len(neg), 1), 3),
            },
            "conflict_preservation_rate": _rate(rows, "conflict_ok"),
            "distractor_beats_gold": _rate(rows, "distractor_above_gold"),
            "latency_ms_p50": data["latency_ms_p50"],
            "latency_ms_p95": data["latency_ms_p95"],
        }
    return out


def _rate(rows, key):
    vals = [r[key] for r in rows if r[key] is not None]
    return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None


def main():
    sep = check_separation()
    if sep:
        print("SEPARATION VIOLATIONS:")
        for s in sep:
            print("  -", s)
        sys.exit(1)
    print("separation check: OK (retrieval reads no gold, references no LLM)\n")

    db = tempfile.mktemp(suffix=".db")
    store = Store(db)
    ingest(store, CORPUS)

    t0 = time.perf_counter()
    chunks = build_chunks(CORPUS, store)
    chunk_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    index = retrievers.Index(chunks, store)
    idx_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    index._build_lsa()
    lsa_s = time.perf_counter() - t0

    per_cfg = evaluate(index, ["R0", "R1", "R2", "R3", "R4", "R5"])
    summary = summarize(per_cfg)
    cost = {"chunks": len(chunks), "chunking_s": round(chunk_s, 3),
            "fts_index_s": round(idx_s, 3), "lsa_build_s": round(lsa_s, 3),
            "lsa_dims": 64, "model_download_mb": 0,
            "dense_method": "LSA (TF-IDF + truncated SVD)"}
    result = {"summary": summary, "cost": cost,
              "detail": {c: per_cfg[c]["rows"] for c in per_cfg}}
    (ROOT / "experiments/x1_results.json").write_text(json.dumps(result, indent=2))

    print(f"corpus chunks: {len(chunks)}  fts index {idx_s:.3f}s  lsa {lsa_s:.3f}s\n")
    hdr = f"{'cfg':4} {'R@1':>6} {'R@5':>6} {'R@10':>6} {'MRR':>6} {'ev@10':>7} {'gap':>6} {'falsesup':>9} {'p95ms':>7}"
    print(hdr); print("-" * len(hdr))
    for cfg, s in summary.items():
        o = s["overall"]
        print(f"{cfg:4} {o['recall@1']:>6} {o['recall@5']:>6} {o['recall@10']:>6} {o['MRR']:>6} "
              f"{o['evidence_correct@10']:>7} {o['evidence_gap']:>6} "
              f"{s['negative']['false_support_rate']:>9} {s['latency_ms_p95']:>7}")

    classes = sorted({c for s in summary.values() for c in s["per_class"]})
    print(f"\nRecall@10 by class:\n{'class':36} " + "  ".join(f"{c:>5}" for c in summary))
    for cl in classes:
        line = f"{cl:36} "
        for cfg in summary:
            v = summary[cfg]["per_class"].get(cl, {}).get("recall@10", 0.0)
            line += f"  {v:>5}"
        print(line)
    store.close()


if __name__ == "__main__":
    main()
