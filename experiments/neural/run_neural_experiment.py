#!/usr/bin/env python3
"""Neural embedding retrieval experiment. Runs on any machine with model access.

Self-contained: needs this repository and network access on first run only.
Records model identity, revision and file hashes; refuses to report a result it
cannot attribute to an exact model.
"""
from __future__ import annotations

import argparse, hashlib, json, os, statistics, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# Extensions the ONNX runtime actually downloads and loads. The first version
# listed only a guessed subset; these are the ones that appear in a real
# fastembed cache and that the runtime opens at inference time.
MODEL_FILE_SUFFIXES = {".onnx", ".onnx_data", ".json", ".txt", ".model",
                       ".bin", ".safetensors", ".vocab", ".merges", ".spm"}


def hash_model_files(model_dir: Path):
    files = []
    if not model_dir or not model_dir.exists():
        return files, 0
    for p in sorted(model_dir.rglob("*")):
        if p.is_file() and (p.suffix in MODEL_FILE_SUFFIXES or p.name in
                            {"tokenizer.json", "config.json", "special_tokens_map.json"}):
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
            files.append({"path": str(p.relative_to(model_dir)),
                          "bytes": p.stat().st_size, "sha256": h.hexdigest()})
    return files, sum(f["bytes"] for f in files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    ap.add_argument("--model-dir", default=None,
                    help="directory of manually supplied model files (optional)")
    ap.add_argument("--out", default="experiments/neural/neural_results.json")
    a = ap.parse_args()

    try:
        import numpy as np
        from fastembed import TextEmbedding
    except ImportError as e:
        sys.exit(f"missing dependency: {e}. See experiments/neural/README.md")

    from experiments.retrieval.chunker import build as build_chunks
    from kgc.pipeline import ingest
    from kgc.store import Store
    import tempfile

    gold = json.loads((ROOT / "eval/x1rerun/X1_RERUN_01_GOLD.json").read_text())
    corpus = ROOT / gold["corpus_root"]

    db = tempfile.mktemp(suffix=".db")
    store = Store(db); ingest(store, corpus)
    chunks = build_chunks(corpus, store)

    t0 = time.perf_counter()
    embedder = TextEmbedding(model_name=a.model)
    load_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    vecs = np.array(list(embedder.embed([c.text for c in chunks])), dtype=np.float32)
    embed_s = time.perf_counter() - t0
    vecs /= (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)

    qlat, rows = [], []
    for q in gold["accepted"]:
        t0 = time.perf_counter()
        qv = np.array(list(embedder.embed([q["query"]]))[0], dtype=np.float32)
        qlat.append((time.perf_counter() - t0) * 1000)
        qv /= (np.linalg.norm(qv) + 1e-9)
        order = np.argsort(-(vecs @ qv))[:10]
        paths = [chunks[i].rel_path for i in order]
        goldset = set(q["answer_units"])
        rank = next((i + 1 for i, p in enumerate(paths) if p in goldset), None)
        rows.append({"id": q["id"], "tier": q["tier"], "rank": rank, "top3": paths[:3]})

    results = {}
    for tier in ("zero_overlap", "partial_overlap", "full_overlap"):
        rs = [r for r in rows if r["tier"] == tier]
        if not rs:
            continue
        results[tier] = {
            "n": len(rs),
            "recall@1": round(sum(1 for r in rs if r["rank"] == 1) / len(rs), 3),
            "recall@10": round(sum(1 for r in rs if r["rank"]) / len(rs), 3),
            "MRR": round(statistics.mean([1 / r["rank"] if r["rank"] else 0 for r in rs]), 3),
            "empty_result_sets": 0,
        }

    cache = Path(a.model_dir) if a.model_dir else Path(
        os.environ.get("FASTEMBED_CACHE_PATH", Path.home() / ".cache" / "fastembed"))
    files, total = hash_model_files(cache)

    zero = results.get("zero_overlap", {}).get("recall@10", 0.0)
    full = results.get("full_overlap", {}).get("recall@10", 0.0)
    out = {
        "model": {"identifier": a.model, "revision": os.environ.get("MODEL_REVISION", "UNRECORDED"),
                  "dimensions": int(vecs.shape[1]), "files": files, "total_bytes": total,
                  "offline_after_first_run": True, "load_seconds": round(load_s, 2)},
        "cost": {"embed_corpus_seconds": round(embed_s, 2), "chunks": len(chunks),
                 "embed_query_ms_p50": round(statistics.median(qlat), 2),
                 "embed_query_ms_p95": round(sorted(qlat)[max(0, int(0.95 * len(qlat)) - 1)], 2),
                 "index_bytes": int(vecs.nbytes)},
        "results": results, "rows": rows,
        "comparison_to_local_run": {
            "R1_bm25": {"zero_overlap_recall@10": 0.1, "partial": 1.0, "full": 1.0},
            "R3_lsa": {"zero_overlap_recall@10": 0.2, "partial": 1.0, "full": 1.0}},
        "decision": {"threshold": "zero_overlap recall@10 >= 0.8 AND no full_overlap regression",
                     "zero_overlap_recall@10": zero, "full_overlap_recall@10": full,
                     "met": bool(zero >= 0.8 and full >= 1.0)},
    }
    # An unattributable neural result must not enter an architectural decision.
    # Previously this was a warning and the result still counted; it is now a
    # hard INVALID.
    revision = out["model"]["revision"]
    problems = []
    if not files:
        problems.append("no model files could be located for hashing "
                        "(set --model-dir or FASTEMBED_CACHE_PATH)")
    if revision == "UNRECORDED":
        problems.append("model revision not recorded (set MODEL_REVISION to the "
                        "exact resolved revision)")
    if problems:
        out["validity"] = {"status": "INVALID", "reasons": problems,
                           "effect": "This result is NOT admissible as evidence for "
                                     "the dense-retrieval decision. Re-run with the "
                                     "model revision and file hashes established."}
        out["decision"]["met"] = False
        out["decision"]["note"] = "forced to false: result is INVALID (unattributable)"
    else:
        out["validity"] = {"status": "VALID",
                           "reasons": [], "model_files_hashed": len(files)}

    Path(a.out).write_text(json.dumps(out, indent=2))
    print(json.dumps({"validity": out["validity"], "results": results,
                      "decision": out["decision"]}, indent=2))
    store.close()
    if problems:
        sys.exit(2)          # non-zero: an INVALID run must not look successful


if __name__ == "__main__":
    main()
