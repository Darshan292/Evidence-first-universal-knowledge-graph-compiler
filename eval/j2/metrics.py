"""J-2 §10-§12, §14, §17 metrics. No single overall score is produced (§21)."""
from __future__ import annotations

import argparse, json, pathlib, statistics, sys

SUPPORTED, PARTIAL, UNSUP = "SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED"
CONTRA, NOTDET = "CONTRADICTED", "NOT_DETERMINABLE"


def consensus(verdicts: dict) -> str:
    """Two judges. Agreement is the verdict; disagreement is reported as such.

    Never silently resolved: a split is its own category and is counted as a
    split everywhere except the conservative reading, which takes the worse of
    the two so a disagreement can never flatter the system.
    """
    vs = [v["verdict"] for v in verdicts.values()]
    if len(set(vs)) == 1:
        return vs[0]
    order = [CONTRA, UNSUP, NOTDET, PARTIAL, SUPPORTED]
    present = [v for v in order if v in vs]
    return present[0] if present else "PARSE_ERROR"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--adjudication", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--provenance", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    run = json.loads(pathlib.Path(a.run).read_text())
    adj = json.loads(pathlib.Path(a.adjudication).read_text())
    gold = {g["id"]: g for g in json.loads(pathlib.Path(a.gold).read_text())["gold"]}
    prov = json.loads(pathlib.Path(a.provenance).read_text())["strata"]

    by_q: dict[str, list] = {}
    for s in adj["statements"]:
        by_q.setdefault(s["id"], []).append(s)

    rows = []
    for r in run["results"]:
        qid = r["id"]
        g, p = gold[qid], prov[qid]
        stmts = by_q.get(qid, [])
        verdicts = [consensus(s["verdicts"]) for s in stmts]
        splits = sum(1 for s in stmts if not s["agree"])

        # §12 deterministic evidence integrity, kept SEPARATE from entailment
        ev_checks = [e for c in (r.get("validation") or {}).get("claims", [])
                     for e in c.get("evidence", [])]
        struct = [s for c in (r.get("validation") or {}).get("claims", [])
                  for s in c.get("structural", [])]

        full_support = bool(verdicts) and all(v == SUPPORTED for v in verdicts)
        rows.append({
            "id": qid, "stratum": p["stratum"], "gold_answerability": g["answerability"],
            "status": r["status"], "exposed": bool(r.get("is_answer")),
            "statements": len(stmts), "verdicts": verdicts, "judge_splits": splits,
            "fully_supported": full_support,
            "has_unsupported": any(v in (UNSUP, CONTRA, NOTDET, PARTIAL) for v in verdicts),
            "evidence_checks": len(ev_checks),
            "evidence_all_ok": all(e.get("ok") for e in ev_checks) if ev_checks else None,
            "structural": [s["status"] for s in struct],
            "attempts": r.get("attempts", 0), "regenerations": r.get("regenerations", 0),
            "latency_seconds": r.get("latency_seconds", 0.0),
            "usage": r.get("usage_delta", {}),
            "abstain_reason": (r.get("abstain_reason") or "")[:200],
        })

    def frac(n, d):
        return {"n": n, "of": d, "rate": round(n / d, 3) if d else None}

    exposed = [x for x in rows if x["exposed"]]
    all_stmts = [v for x in exposed for v in x["verdicts"]]
    answerable = [x for x in rows if x["gold_answerability"] == "ANSWERABLE"]
    unestablishable = [x for x in rows if x["gold_answerability"] in ("PARTIAL", "UNANSWERABLE")]
    fresh = [x for x in rows if x["stratum"] == "FRESH"]
    fresh_answerable = [x for x in fresh if x["gold_answerability"] == "ANSWERABLE"]

    # §10 grounded-but-wrong: passed the deterministic gate, not actually entailed
    gbw_stmt = 0
    for x in exposed:
        if x["evidence_all_ok"]:
            gbw_stmt += sum(1 for v in x["verdicts"] if v in (UNSUP, CONTRA, NOTDET, PARTIAL))
    gbw_ans = [x for x in exposed if x["evidence_all_ok"] and x["has_unsupported"]]

    lat = [x["latency_seconds"] for x in rows if x["latency_seconds"]]
    inp = [x["usage"].get("input_tokens", 0) for x in rows]
    outp = [x["usage"].get("output_tokens", 0) for x in rows]

    def pct(v, q):
        return round(statistics.quantiles(v, n=100)[q - 1], 3) if len(v) > 2 else (max(v) if v else 0)

    m = {
      "run": run["run_label"], "generator": run["provider"], "judges": adj["judge_models"],
      "adjudication": adj["labelled"],
      "DETERMINISTIC_EVIDENCE_INTEGRITY": {
        "exposed_answers": len(exposed),
        "exposed_answers_all_evidence_reverified": sum(1 for x in exposed if x["evidence_all_ok"]),
        "evidence_checks_total": sum(x["evidence_checks"] for x in exposed),
        "note": "This is grounding, not entailment. Kept separate on purpose (§12).",
      },
      "SEMANTIC_ENTAILMENT": {
        "material_statements_exposed": len(all_stmts),
        "SUPPORTED": frac(all_stmts.count(SUPPORTED), len(all_stmts)),
        "PARTIALLY_SUPPORTED": frac(all_stmts.count(PARTIAL), len(all_stmts)),
        "UNSUPPORTED": frac(all_stmts.count(UNSUP), len(all_stmts)),
        "CONTRADICTED": frac(all_stmts.count(CONTRA), len(all_stmts)),
        "NOT_DETERMINABLE": frac(all_stmts.count(NOTDET), len(all_stmts)),
        "answer_level_full_support": frac(sum(1 for x in exposed if x["fully_supported"]),
                                          len(exposed)),
        "judge_disagreement_statements": frac(sum(x["judge_splits"] for x in exposed),
                                              len(all_stmts)),
      },
      "GROUNDED_BUT_WRONG": {
        "statements": frac(gbw_stmt, len(all_stmts)),
        "answers": frac(len(gbw_ans), len(exposed)),
        "ids": [x["id"] for x in gbw_ans],
        "definition": ("every cited evidence id existed, was retrieved for this question "
                       "and still matched the source bytes, yet the statement is not "
                       "entailed by it"),
      },
      "SAFETY_UNSAFE_EXPOSURE": {
        "questions_source_cannot_establish": len(unestablishable),
        "of_those_exposed_a_substantive_answer": frac(
            sum(1 for x in unestablishable if x["exposed"]), len(unestablishable)),
        "ids_exposed": [x["id"] for x in unestablishable if x["exposed"]],
        "appropriate_abstention": frac(sum(1 for x in unestablishable if not x["exposed"]),
                                       len(unestablishable)),
      },
      "USEFUL_ANSWER_AVAILABILITY": {
        "gold_answerable": len(answerable),
        "exposed": frac(sum(1 for x in answerable if x["exposed"]), len(answerable)),
        "exposed_and_fully_supported": frac(
            sum(1 for x in answerable if x["exposed"] and x["fully_supported"]), len(answerable)),
        "FRESH_stratum_only": {
          "gold_answerable": len(fresh_answerable),
          "exposed": frac(sum(1 for x in fresh_answerable if x["exposed"]), len(fresh_answerable)),
          "exposed_and_fully_supported": frac(
              sum(1 for x in fresh_answerable if x["exposed"] and x["fully_supported"]),
              len(fresh_answerable)),
        },
        "abstained_overall": frac(sum(1 for x in rows if not x["exposed"]), len(rows)),
      },
      "STRUCTURAL_CHECKS": {
        "SUPPORTED": sum(s.count("SUPPORTED") for s in (x["structural"] for x in rows)),
        "EXPLICIT_CONTRADICTION": sum(s.count("EXPLICIT_CONTRADICTION")
                                      for s in (x["structural"] for x in rows)),
        "NOT_ESTABLISHED": sum(s.count("NOT_ESTABLISHED")
                               for s in (x["structural"] for x in rows)),
        "answers_carrying_a_structural_assertion":
            sum(1 for x in exposed if x["structural"]),
      },
      "COST_LATENCY": {
        "latency_seconds_median": round(statistics.median(lat), 3) if lat else None,
        "latency_seconds_p95": pct(lat, 95),
        "input_tokens_total": sum(inp), "output_tokens_total": sum(outp),
        "input_tokens_median": statistics.median(inp) if inp else 0,
        "regenerations_total": sum(x["regenerations"] for x in rows),
        "cache_hits": sum(x["usage"].get("cache_hits", 0) for x in rows),
        "harness_paced_seconds_excluded_from_latency": run.get("harness_paced_seconds"),
        "note": "latency is the provider's own measured API time; harness pacing excluded",
      },
      "per_question": rows,
    }
    pathlib.Path(a.out).write_text(json.dumps(m, indent=2) + "\n")
    for k in ("DETERMINISTIC_EVIDENCE_INTEGRITY", "SEMANTIC_ENTAILMENT", "GROUNDED_BUT_WRONG",
              "SAFETY_UNSAFE_EXPOSURE", "USEFUL_ANSWER_AVAILABILITY", "STRUCTURAL_CHECKS",
              "COST_LATENCY"):
        print(f"\n== {k} ==")
        print(json.dumps(m[k], indent=2)[:1400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
