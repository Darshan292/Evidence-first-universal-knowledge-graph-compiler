"""Abstention benchmark. Follows eval/ABSTENTION_PREREGISTRATION.md exactly.

CALIBRATION selects theta. VALIDATION compares designs. TEST runs once.
"""
from __future__ import annotations

import json, sys, tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.retrieval.chunker import build as build_chunks
from experiments.retrieval.retrievers import Index
from experiments.retrieval.support import ABSTAIN, EXPOSE, EXPOSE_CONFLICTED, SupportGate
from kgc.pipeline import ingest
from kgc.store import Store

GOLD = json.loads((ROOT / "eval/ABSTENTION_GOLD.json").read_text())
CORPUS = ROOT / GOLD["corpus_root"]
THETAS = [50, 60, 70, 75, 80, 85, 90, 95]
MAX_FALSE_ABSTENTION_CAL = 0.30


def queries(split):
    return [q for q in GOLD["queries"] if q["split"] == split]


def score(gate, index, qs):
    rows = []
    for q in qs:
        hits = index.r1(q["query"])
        d = gate.decide(q["query"], hits)
        correct = d.outcome in q["expected_decision"]
        rows.append({"id": q["id"], "kind": q["kind"],
                     "class": q.get("negative_class") or q.get("positive_class"),
                     "decision": d.outcome, "expected": q["expected_decision"],
                     "correct": correct, "reason": d.reason,
                     "failed_condition": d.failed_condition,
                     "evidence": (d.evidence_text or "")[:120]})
    neg = [r for r in rows if r["kind"] == "negative"]
    pos = [r for r in rows if r["kind"] == "positive"]
    false_support = [r for r in neg if not r["correct"]]
    false_abstain = [r for r in pos if r["decision"] == ABSTAIN]
    by_class = defaultdict(lambda: [0, 0])
    for r in neg:
        by_class[r["class"]][1] += 1
        if not r["correct"]:
            by_class[r["class"]][0] += 1
    return {
        "n_neg": len(neg), "n_pos": len(pos),
        "false_support_rate": round(len(false_support) / max(len(neg), 1), 3),
        "false_abstention_rate": round(len(false_abstain) / max(len(pos), 1), 3),
        "acceptance_rate": round(sum(1 for r in pos if r["decision"] != ABSTAIN) / max(len(pos), 1), 3),
        "false_support_by_class": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_class.items())},
        "class_C_false_support": round(
            by_class["C"][0] / by_class["C"][1], 3) if by_class["C"][1] else None,
        "unsupported_exposure": sum(
            1 for r in rows if r["decision"] in (EXPOSE, EXPOSE_CONFLICTED) and not r["evidence"]),
        "rows": rows,
    }


def calibrate(index, chunks, store, design):
    """Pre-registered rule: discard theta with cal false-abstention > 0.30;
    pick lowest false-support; tie -> lower false-abstention; tie -> lower theta."""
    cal = queries("CALIBRATION")
    sweep = []
    for th in THETAS:
        g = SupportGate(chunks, store, design=design, idf_percentile=th)
        s = score(g, index, cal)
        sweep.append({"theta": th, "false_support": s["false_support_rate"],
                      "false_abstention": s["false_abstention_rate"],
                      "class_C": s["class_C_false_support"]})
    eligible = [s for s in sweep if s["false_abstention"] <= MAX_FALSE_ABSTENTION_CAL]
    pool = eligible or sweep
    chosen = sorted(pool, key=lambda s: (s["false_support"], s["false_abstention"], s["theta"]))[0]
    return chosen["theta"], sweep, bool(eligible)


def main():
    db = tempfile.mktemp(suffix=".db")
    store = Store(db)
    ingest(store, CORPUS)
    chunks = build_chunks(CORPUS, store)
    index = Index(chunks, store)

    out = {"calibration": {}, "validation": {}, "test": None}

    print("=== STEP 1: calibrate theta on CALIBRATION only ===")
    chosen = {}
    for design in ("G0", "G1", "G2", "G3", "G4"):
        th, sweep, had_eligible = calibrate(index, chunks, store, design)
        chosen[design] = th
        out["calibration"][design] = {"chosen_theta": th, "sweep": sweep,
                                      "any_theta_met_availability": had_eligible}
        print(f"  {design}: theta={th}  (availability-eligible thetas existed: {had_eligible})")
        for s in sweep:
            print(f"      p{s['theta']:<3} false_support={s['false_support']:<6} "
                  f"false_abstention={s['false_abstention']:<6} classC={s['class_C']}")

    print("\n=== STEP 2: compare designs on VALIDATION ===")
    val = queries("VALIDATION")
    hdr = f"  {'design':7} {'theta':6} {'falseSup':9} {'classC':8} {'falseAbs':9} {'accept':8}"
    print(hdr)
    for design in ("G0", "G1", "G2", "G3", "G4"):
        g = SupportGate(chunks, store, design=design, idf_percentile=chosen[design])
        s = score(g, index, val)
        out["validation"][design] = {k: v for k, v in s.items() if k != "rows"}
        out["validation"][design]["theta"] = chosen[design]
        print(f"  {design:7} {chosen[design]:<6} {s['false_support_rate']:<9} "
              f"{str(s['class_C_false_support']):<8} {s['false_abstention_rate']:<9} "
              f"{s['acceptance_rate']:<8}")

    best = min(("G1", "G2", "G3", "G4"),
               key=lambda d: (out["validation"][d]["false_support_rate"],
                              out["validation"][d]["false_abstention_rate"]))
    print(f"\n  design selected on VALIDATION: {best} (theta={chosen[best]})")

    print("\n=== STEP 3: TEST, run once ===")
    g = SupportGate(chunks, store, design=best, idf_percentile=chosen[best])
    t = score(g, index, queries("TEST"))
    out["test"] = {"design": best, "theta": chosen[best],
                   **{k: v for k, v in t.items()}}
    for k in ("n_neg", "n_pos", "false_support_rate", "class_C_false_support",
              "false_abstention_rate", "acceptance_rate", "unsupported_exposure",
              "false_support_by_class"):
        print(f"  {k:26} {t[k]}")

    print("\n  per-query TEST decisions:")
    for r in t["rows"]:
        mark = "ok " if r["correct"] else "FAIL"
        print(f"   {mark} {r['id']:6} {r['class']:14} {r['decision']:18} {r['reason'][:52]}")

    (ROOT / "experiments/abstention_results.json").write_text(json.dumps(out, indent=2))
    store.close()


if __name__ == "__main__":
    main()
