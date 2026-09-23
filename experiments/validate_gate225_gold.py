"""Gold integrity for Gate 2.25. Corpus + gold only; never imports kgc."""
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
g = json.loads((ROOT / "eval/GATE225_GOLD.json").read_text())
croot = ROOT / g["corpus_root"]
errs = []
for q in g["queries"]:
    if not q["answerable"]:
        if q.get("answer_units"): errs.append(f"{q['id']}: negative lists answer units")
        continue
    for u in q["answer_units"]:
        if not (croot / u).exists(): errs.append(f"{q['id']}: missing unit {u}")
    ev = q.get("evidence_must_contain")
    if ev and not any(ev in (croot / u).read_text(encoding="utf-8", errors="replace")
                      for u in q["answer_units"] if (croot / u).exists()):
        errs.append(f"{q['id']}: evidence {ev!r} not in any answer unit")
    if not q.get("claim_target"): errs.append(f"{q['id']}: no claim_target")
print(f"gate2.25 gold: {len(g['queries'])} queries, "
      f"{sum(1 for q in g['queries'] if q['answerable'])} answerable")
if errs:
    print("\nFAILURES:"); [print("  -", e) for e in errs[:15]]
    print(f"  ... {len(errs)} total" if len(errs) > 15 else "")
    sys.exit(1)
print("gold integrity: OK — every unit exists, every evidence string verbatim in real source")
