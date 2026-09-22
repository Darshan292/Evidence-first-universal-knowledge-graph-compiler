"""Gate 1 baseline measurements. Baselines, not benchmarks to win."""
from __future__ import annotations
import json, os, resource, shutil, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kgc.pipeline import ingest
from kgc.store import Store

TEMPLATE = '''"""Module {i}."""
import os
from pkg.mod{j} import helper{j}

TIMEOUT_{i} = {i}
NAME_{i} = "svc{i}"


class Service{i}:
    """Service {i}."""

    def __init__(self, cfg):
        self.cfg = cfg

    def run(self, payload):
        """Run it."""
        value = helper{j}(payload)
        return self.finish(value)

    def finish(self, value):
        return os.environ.get("K", str(value))


def make{i}():
    return Service{i}(TIMEOUT_{i})


def helper{i}(x):
    return x * 2
'''

def build_corpus(root: Path, n: int) -> int:
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    for i in range(n):
        d = root / f"pkg{i // 50}"
        d.mkdir(exist_ok=True)
        p = d / f"mod{i}.py"
        p.write_text(TEMPLATE.format(i=i, j=(i + 1) % max(n, 2)))
        total += p.stat().st_size
    return total

def mb(x): return round(x / 1e6, 2)
def rss_mb(): return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)

def run_scale(n: int) -> dict:
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus = tmp / "corpus"
        src_bytes = build_corpus(corpus, n)
        db = str(tmp / "kg.sqlite")

        t0 = time.perf_counter(); s = Store(db); rep = ingest(s, corpus)
        cold = time.perf_counter() - t0
        counts = s.counts(); s.close()
        size = os.path.getsize(db)

        t0 = time.perf_counter(); s = Store(db); ingest(s, corpus)
        warm = time.perf_counter() - t0; s.close()

        db2 = str(tmp / "kg2.sqlite")
        s = Store(db2); ingest(s, corpus); s.close()
        import sqlite3
        def snap(p):
            c = sqlite3.connect(p)
            out = {t: {tuple(r) for r in c.execute(q)} for t, q in (
                ("symbol", "SELECT symbol_id,qualified_name FROM symbol"),
                ("claim", "SELECT claim_id,predicate,establishment FROM claim"),
                ("evidence", "SELECT evidence_id,verification_strength FROM evidence"))}
            c.close(); return out
        reproducible = snap(db) == snap(db2)

        t0 = time.perf_counter(); s = Store(db); v = s.check_invariants(); s.close()
        verify_s = time.perf_counter() - t0

        return {"files": n, "source_mb": mb(src_bytes), "db_mb": mb(size),
                "cold_index_s": round(cold, 2),
                "files_per_s": round(n / cold, 1),
                "source_kb_per_s": round(src_bytes / 1024 / cold, 1),
                "warm_reindex_s": round(warm, 2),
                "warm_speedup": round(cold / warm, 2) if warm else None,
                "db_bytes_per_source_byte": round(size / max(src_bytes, 1), 2),
                "symbols": counts["symbol"], "claims": counts["claim"],
                "evidence": counts["evidence"],
                "claims_per_file": round(counts["claim"] / n, 1),
                "verify_s": round(verify_s, 3),
                "invariant_violations": len(v),
                "reproducible_across_independent_runs": reproducible,
                "peak_rss_mb": rss_mb()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def crash_matrix() -> dict:
    from kgc.pipeline import CrashPoint
    tmp = Path(tempfile.mkdtemp())
    try:
        corpus = tmp / "c"; build_corpus(corpus, 40)
        ref = str(tmp / "ref.db"); s = Store(ref); ingest(s, corpus); s.close()
        import sqlite3
        def snap(p):
            c = sqlite3.connect(p)
            out = {tuple(r) for r in c.execute("SELECT claim_id,predicate FROM claim")}
            c.close(); return out
        want = snap(ref)
        out = {}
        for pct, nth in (("10%", 4), ("30%", 12), ("50%", 20), ("90%", 36)):
            db = str(tmp / f"c{nth}.db"); s = Store(db)
            t0 = time.perf_counter()
            try: ingest(s, corpus, crash_at=("during_evidence", nth))
            except CrashPoint: pass
            s.close()
            s = Store(db); ingest(s, corpus)
            recover = time.perf_counter() - t0
            viol = [v for v in s.check_invariants() if "RUNNING" not in v]
            got = snap(db); s.close()
            out[pct] = {"recovered_identical": got == want,
                        "violations": viol,
                        "total_s": round(recover, 2)}
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    results = {"scales": [run_scale(n) for n in (10, 100, 1000)],
               "crash_recovery": crash_matrix()}
    # real corpus: the compiler itself
    repo = Path(__file__).resolve().parents[1] / "kgc"
    tmp = Path(tempfile.mkdtemp())
    try:
        db = str(tmp / "self.db"); t0 = time.perf_counter()
        s = Store(db); rep = ingest(s, repo); dt = time.perf_counter() - t0
        c = s.counts(); v = s.check_invariants(); s.close()
        results["self_ingest"] = {"files": rep.seen, "seconds": round(dt, 2),
                                  "symbols": c["symbol"], "claims": c["claim"],
                                  "db_mb": mb(os.path.getsize(db)),
                                  "invariant_violations": len(v)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(results, indent=2))
