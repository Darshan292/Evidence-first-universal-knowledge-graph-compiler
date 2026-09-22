"""Gold integrity check. Reads corpus + gold ONLY. Never imports retrieval."""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errs: list[str] = []

g = json.loads((ROOT / "eval/EVALUATION_GOLD.json").read_text())
croot = ROOT / g["corpus_root"]
for q in g["queries"]:
    for unit in q["answer_units"] + q.get("also_relevant", []):
        if not (croot / unit).exists():
            errs.append(f"{q['id']}: answer unit missing from corpus: {unit}")
    ev = q.get("evidence_must_contain")
    if ev:
        hit = any(ev in (croot / u).read_text(encoding="utf-8")
                  for u in q["answer_units"] if (croot / u).exists())
        if not hit:
            errs.append(f"{q['id']}: evidence_must_contain {ev!r} not present in any answer unit")
    if q["answerable"] and not q["answer_units"]:
        errs.append(f"{q['id']}: answerable but has no answer units")
    if not q["answerable"] and q["answer_units"]:
        errs.append(f"{q['id']}: unanswerable but lists answer units")

c = json.loads((ROOT / "eval/CODE_ANALYSIS_GOLD.json").read_text())
rroot = ROOT / c["corpus_root"]
for cs in c["call_sites"]:
    mod = cs["in"].rsplit(".", 1)[0]
    cand = [rroot / (mod.replace(".", "/") + ".py"),
            rroot / (mod.split(".")[0] + ".py"),
            rroot / (mod.replace(".", "/") + "/__init__.py")]
    src = next((p for p in cand if p.exists()), None)
    if src is None:
        errs.append(f"{cs['id']}: module for {cs['in']} not found in corpus")
        continue
    if cs["call_text"].split(".")[-1] not in src.read_text():
        errs.append(f"{cs['id']}: call text {cs['call_text']!r} not found in {src.name}")

print(f"gold queries: {len(g['queries'])}  call sites: {len(c['call_sites'])}  "
      f"import edges: {len(c['import_edges'])}")
if errs:
    print("\nGOLD INTEGRITY FAILURES:")
    for e in errs: print("  -", e)
    sys.exit(1)
print("gold integrity: OK -- every answer unit exists and every required evidence string is present in source")
