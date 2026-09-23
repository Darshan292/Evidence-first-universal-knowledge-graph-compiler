"""J-1 scoring. Applies the measures pre-registered in J1_PROTOCOL.md §5.

Inputs, all frozen before it runs:
  experiments/j1_raw_run{1,2}.json   decisions + evidence checks
  eval/J1_GOLD.json                  labels, established from source
  eval/J1_JUDGEMENTS.json            the manual verdicts §5C/§5D require,
                                     plus the §6 root cause per failure

It computes; it does not decide. Every manual verdict lives in the judgements
file so it can be read back and argued with.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXPOSED = {"EXPOSE", "EXPOSE_CONFLICTED"}
UNSAFE_LABELS = {"unsupported", "ambiguous", "out_of_scope"}
TRUSTED = {"DERIVED", "CONFIRMED"}


def load(p: Path):
    return json.loads(p.read_text())


def rate(n: int, d: int):
    return round(n / d, 4) if d else None


def score() -> dict:
    raw = load(ROOT / "experiments/j1_raw_run1.json")
    gold = {g["id"]: g for g in load(ROOT / "eval/J1_GOLD.json")["gold"]}
    jpath = ROOT / "eval/J1_JUDGEMENTS.json"
    judged = {j["id"]: j for j in load(jpath)["judgements"]} if jpath.exists() else {}
    rows = {r["id"]: r for r in raw["rows"]}
    assert set(rows) == set(gold), "query set and gold disagree"

    answerable = [i for i in rows if gold[i]["label"] == "answerable"]
    unsafe = [i for i in rows if gold[i]["label"] in UNSAFE_LABELS]
    ambiguous = [i for i in rows if gold[i]["label"] == "ambiguous"]

    # ── A. parse availability ────────────────────────────────────────────
    parse = Counter(rows[i]["parse_status"] for i in rows)
    parse_answerable = Counter(rows[i]["parse_status"] for i in answerable)
    parser_blocked = [i for i in answerable
                      if rows[i]["failed_invariant"] == "INV-1_constraints"]
    A = {
        "parse_status_all": dict(parse),
        "parse_status_answerable": dict(parse_answerable),
        "full_parse_rate": rate(parse["PARSED"], len(rows)),
        "full_parse_rate_answerable": rate(parse_answerable["PARSED"], len(answerable)),
        "parser_caused_abstention": rate(len(parser_blocked), len(answerable)),
        "parser_blocked_ids": sorted(parser_blocked),
        "hand_written_baseline": 0.50,
        "template_derived_non_baseline": 0.984,
    }

    # ── B. safety ────────────────────────────────────────────────────────
    false_support = [i for i in unsafe if rows[i]["decision"] in EXPOSED]
    unsafe_exposure = [i for i in unsafe if rows[i]["decision"] in EXPOSED
                       and not all(h["establishment"] in TRUSTED for h in rows[i]["hits"])]
    misresolved = [i for i in ambiguous if rows[i]["decision"] in EXPOSED]
    by_label = {}
    for lab in sorted(UNSAFE_LABELS):
        ids = [i for i in rows if gold[i]["label"] == lab]
        bad = [i for i in ids if rows[i]["decision"] in EXPOSED]
        by_label[lab] = {"n": len(ids), "exposed": len(bad),
                         "false_support": rate(len(bad), len(ids)), "ids": sorted(bad)}
    B = {
        "n_must_not_answer": len(unsafe),
        "false_support": rate(len(false_support), len(unsafe)),
        "false_support_ids": sorted(false_support),
        "unsafe_exposure": rate(len(unsafe_exposure), len(unsafe)),
        "incorrect_resolution_on_ambiguous": rate(len(misresolved), len(ambiguous)),
        "by_label": by_label,
        "abstained_correctly": rate(len(unsafe) - len(false_support), len(unsafe)),
    }

    # ── C. utility ───────────────────────────────────────────────────────
    exposed = [i for i in answerable if rows[i]["decision"] in EXPOSED]
    ans_ok = [i for i in exposed if judged.get(i, {}).get("answer_correct") is True]
    claim_ok = [i for i in exposed if judged.get(i, {}).get("claim_correct") is True]
    C = {
        "n_answerable": len(answerable),
        "exposure_rate": rate(len(exposed), len(answerable)),
        "exposed_ids": sorted(exposed),
        "answer_correctness": rate(len(ans_ok), len(exposed)),
        "answer_correct_ids": sorted(ans_ok),
        "claim_correctness": rate(len(claim_ok), len(exposed)),
        "useful_end_to_end": rate(len(ans_ok), len(answerable)),
        "decisions_on_answerable": dict(Counter(rows[i]["decision"] for i in answerable)),
    }

    # ── D. evidence fidelity ─────────────────────────────────────────────
    checks = [(i, e) for i in exposed for e in rows[i]["evidence_checks"]]
    suff = [i for i in exposed if judged.get(i, {}).get("evidence_sufficient") is True]
    D = {
        "exposed_queries_checked": len(exposed),
        "evidence_records_checked": len(checks),
        "evidence_localized": rate(sum(1 for _i, e in checks if e["localized"]), len(checks)),
        "evidence_exact": rate(sum(1 for _i, e in checks if e["exact"]), len(checks)),
        "evidence_failures": [{"id": i, **e} for i, e in checks
                              if not (e["localized"] and e["exact"])],
        "evidence_sufficient": rate(len(suff), len(exposed)),
        "evidence_sufficient_ids": sorted(suff),
    }

    # ── error causes (§6) ────────────────────────────────────────────────
    failures = {}
    for i in sorted(rows):
        j = judged.get(i, {})
        if j.get("root_cause"):
            failures[i] = {"label": gold[i]["label"], "decision": rows[i]["decision"],
                           "root_cause": j["root_cause"], "note": j.get("note", "")}
    causes = Counter(f["root_cause"] for f in failures.values())

    return {
        "queries_sha256": raw["queries_sha256"],
        "gold_sha256": hashlib.sha256((ROOT / "eval/J1_GOLD.json").read_bytes()).hexdigest(),
        "n_queries": len(rows),
        "gold_distribution": dict(Counter(g["label"] for g in gold.values())),
        "A_parse_availability": A,
        "B_safety": B,
        "C_utility": C,
        "D_evidence_fidelity": D,
        "failure_causes": dict(causes),
        "failures": failures,
    }


if __name__ == "__main__":
    out = score()
    (ROOT / "J1_RESULTS.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "failures"}, indent=2)[:4000])
