"""C-2: measure cross-module resolution against independently-authored gold.

Reads: eval/rescorpus (corpus) + eval/CODE_ANALYSIS_GOLD.json (gold) + the
extractor output. Does not modify either. Never tunes against gold.
"""
from __future__ import annotations
import json, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kgc.pipeline import ingest
from kgc.store import Store

GOLD = json.loads((ROOT / "eval/CODE_ANALYSIS_GOLD.json").read_text())
CORPUS = ROOT / GOLD["corpus_root"]


def extract():
    db = tempfile.mktemp(suffix=".db")
    s = Store(db); ingest(s, CORPUS)
    rows = [dict(r) for r in s.con.execute("""
        SELECT s.qualified_name AS from_qn, r.to_name, r.resolution, r.reason,
               cl.predicate, cl.object_id, s2.qualified_name AS to_qn
          FROM reference r
          JOIN claim cl USING(claim_id)
          JOIN symbol s  ON s.symbol_id  = cl.subject_id
          LEFT JOIN symbol s2 ON s2.symbol_id = cl.object_id
         WHERE cl.predicate='CALLS'""")]
    s.close()
    return rows


def evaluate(rows):
    idx = {}
    for r in rows:
        idx.setdefault((r["from_qn"], r["to_name"]), []).append(r)

    results, conf = [], {"TP": 0, "WRONG_TARGET": 0, "UNDER": 0, "OVER": 0, "MISSING": 0}
    for cs in GOLD["call_sites"]:
        got = idx.get((cs["in"], cs["call_text"]), [])
        exp_class = cs["should_resolve"]
        acceptable = set(cs.get("acceptable_targets", []))
        if cs["true_target"]:
            acceptable.add(cs["true_target"])
        forbidden = set(cs.get("must_not_target", []))

        if not got:
            outcome, actual_class, actual_target = "MISSING", None, None
            conf["MISSING"] += 1
        else:
            g = got[0]
            actual_class, actual_target = g["resolution"], g["to_qn"]
            if actual_target in forbidden:
                outcome = "WRONG_TARGET"; conf["WRONG_TARGET"] += 1
            elif exp_class == "DETERMINISTIC" and actual_class == "DETERMINISTIC":
                outcome = "TP" if (actual_target in acceptable) else "WRONG_TARGET"
                conf["TP" if outcome == "TP" else "WRONG_TARGET"] += 1
            elif exp_class == "UNRESOLVED" and actual_class == "UNRESOLVED":
                outcome = "TP"; conf["TP"] += 1
            elif exp_class == "HEURISTIC" and actual_class in ("HEURISTIC", "DETERMINISTIC"):
                outcome = "TP" if actual_class == "HEURISTIC" or actual_target in acceptable else "WRONG_TARGET"
                conf["TP" if outcome == "TP" else "WRONG_TARGET"] += 1
            elif exp_class == "DETERMINISTIC":
                outcome = "UNDER"; conf["UNDER"] += 1      # honest but weaker than achievable
            else:
                outcome = "OVER"; conf["OVER"] += 1        # claimed more than warranted
        results.append({"id": cs["id"], "case": cs["case"], "expected": exp_class,
                        "actual": actual_class, "target": actual_target,
                        "true_target": cs["true_target"], "outcome": outcome})

    det_gold = [r for r in results if r["expected"] == "DETERMINISTIC"]
    det_ok = [r for r in det_gold if r["outcome"] == "TP"]
    unres_gold = [r for r in results if r["expected"] == "UNRESOLVED"]
    unres_ok = [r for r in unres_gold if r["outcome"] == "TP"]
    wrong = [r for r in results if r["outcome"] == "WRONG_TARGET"]

    return {
        "results": results,
        "confusion": conf,
        "deterministic_recall": round(len(det_ok) / max(len(det_gold), 1), 3),
        "deterministic_gold_n": len(det_gold),
        "unresolved_correct_rate": round(len(unres_ok) / max(len(unres_gold), 1), 3),
        "false_positive_targets": len(wrong),
        "false_positive_rate": round(len(wrong) / max(len(results), 1), 3),
        "under_resolved": conf["UNDER"],
        "missing": conf["MISSING"],
    }


if __name__ == "__main__":
    out = evaluate(extract())
    print(f"{'id':6} {'expected':14} {'actual':14} {'outcome':13} case")
    for r in out["results"]:
        print(f"{r['id']:6} {r['expected']:14} {str(r['actual']):14} {r['outcome']:13} {r['case'][:46]}")
    print()
    for k in ("deterministic_recall", "deterministic_gold_n", "unresolved_correct_rate",
              "false_positive_targets", "false_positive_rate", "under_resolved", "missing"):
        print(f"  {k:26} {out[k]}")
    print(f"\n  D-F threshold: implement resolver if deterministic_recall < 0.80 "
          f"-> {'TRIGGERED' if out['deterministic_recall'] < 0.80 else 'not triggered'}")
    (ROOT / "experiments/c2_baseline.json").write_text(json.dumps(out, indent=2))
