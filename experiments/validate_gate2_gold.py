"""Gold integrity for Gate 2. Reads corpus + gold only. Never reads retrieval."""
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
g = json.loads((ROOT / "eval/GATE2_GOLD.json").read_text())
croot = ROOT / g["corpus_root"]
errs = []
for q in g["queries"]:
    if q["kind"] == "negative":
        if q.get("answer_units"): errs.append(f"{q['id']}: negative lists answer units")
        continue
    for u in q["answer_units"]:
        if not (croot / u).exists(): errs.append(f"{q['id']}: missing unit {u}")
    ev = q.get("evidence_must_contain")
    if ev and not any(ev in (croot / u).read_text(encoding="utf-8")
                      for u in q["answer_units"] if (croot / u).exists()):
        errs.append(f"{q['id']}: evidence {ev!r} not present in any answer unit")
    if not q.get("claim_target"): errs.append(f"{q['id']}: positive has no claim_target")
print(f"gate2 gold: {len(g['queries'])} queries "
      f"({sum(1 for q in g['queries'] if q['answerable'])} answerable)")
if errs:
    print("\nGOLD INTEGRITY FAILURES:")
    for e in errs: print("  -", e)
    sys.exit(1)
print("gold integrity: OK — every answer unit exists and every evidence string is verbatim in the real source")
