"""J-2 runner. Executes the FROZEN system over the FROZEN query set.

It changes nothing about the system: it imports kgq.answer.ask, the same entry
point the workbench uses, and records what comes back. No per-question logic,
no prompt edits, no retrieval tweaks -- §18.
"""
from __future__ import annotations

import argparse, hashlib, json, os, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from kgq.answer import ANSWER, Budget, ask
from kgq.provider import Provider

ROOT = pathlib.Path(__file__).resolve().parents[2]
QUERIES = ROOT / "eval/j2/J2_QUERIES.json"
CORPUS = (ROOT / "eval/corpus3").resolve()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-label", required=True)
    ap.add_argument("--cache", default="")
    a = ap.parse_args()

    qraw = QUERIES.read_bytes()
    qs = json.loads(qraw)["questions"]
    provider = Provider.from_env(cache_path=a.cache or None)

    results = []
    t_all = time.perf_counter()
    for i, q in enumerate(qs, 1):
        t0 = time.perf_counter()
        u = getattr(provider, "usage", None)
        before = u.as_dict() if u is not None else {}
        try:
            r = ask(q["question"], db_path=a.db, corpus_root=str(CORPUS),
                    provider=provider, budget=Budget())
            d = r.as_dict()
            d["is_answer"] = r.status == ANSWER
            d["error"] = None
        except Exception as exc:                 # a crash is a result, not a gap
            d = {"question": q["question"], "status": "RUNNER_ERROR",
                 "error": f"{type(exc).__name__}: {exc}", "is_answer": False}
        after = u.as_dict() if u is not None else {}
        d["id"] = q["id"]
        d["wall_seconds"] = round(time.perf_counter() - t0, 3)
        d["usage_delta"] = {k: after.get(k, 0) - before.get(k, 0)
                            for k in set(after) | set(before)}
        results.append(d)
        print(f"  [{i:2}/{len(qs)}] {q['id']}  {d['status']:18} {d['wall_seconds']:6.2f}s",
              flush=True)

    out = {
        "run_label": a.run_label,
        "queries_sha256": hashlib.sha256(qraw).hexdigest(),
        "gold_sha256": hashlib.sha256((ROOT / "eval/j2/J2_GOLD.json").read_bytes()).hexdigest(),
        "corpus_content_sha256": "19f4028a0a9f15b4e77d41ad489ee3368b72b309c9ac2836b1d34941fc32d35f",
        "database": a.db,
        "database_sha256": hashlib.sha256(pathlib.Path(a.db).read_bytes()).hexdigest(),
        "provider": {"base_url": provider.base_url, "model": provider.model,
                     "temperature": os.environ.get("KGQ_TEMPERATURE", "provider default"),
                     "cache": a.cache or "disabled"},
        "budget": Budget().as_dict(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time())),
        "total_seconds": round(time.perf_counter() - t_all, 2),
        "results": results,
    }
    pathlib.Path(a.out).write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nwrote {a.out}  ({out['total_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
