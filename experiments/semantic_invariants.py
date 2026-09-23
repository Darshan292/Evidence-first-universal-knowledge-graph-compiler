"""Gate 2.5 executable invariants. Runnable over any compiled database.

These check properties the evaluation metrics could NOT see: 30 queries were
mis-reported as conflicts while every headline number stayed identical, because
the gold accepted either outcome.
"""
from __future__ import annotations
import sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.retrieval.claimfirst import ClaimIndex, _conflict, decide
from kgc.artifact_identity import canonical_path
from kgc.pipeline import ingest
from kgc.predicates import CANONICAL, may_contradict
from kgc.store import Store


def check(store) -> list[str]:
    v: list[str] = []
    index = ClaimIndex(store)

    # I-1 conflict: a MULTI_VALUED predicate cannot contradict itself by having
    # two valid objects.
    for pred, spec in CANONICAL.items():
        if spec.may_contradict:
            continue
        rows = store.con.execute("""
            SELECT cl.subject_id FROM claim cl WHERE cl.predicate=?
             GROUP BY cl.subject_id HAVING count(DISTINCT
               COALESCE(cl.object_id, cl.object_literal)) > 1 LIMIT 25""", (pred,)).fetchall()
        for r in rows:
            hits = index.claims_from(r["subject_id"], pred)
            if _conflict(hits)["state"] == "CONTRADICTS":
                v.append(f"I-1 {pred} (MULTI_VALUED) reported CONTRADICTS for a subject "
                         f"with several valid objects")
                break

    # I-2 scope: a scoped decision must not expose evidence from another artifact.
    paths = index.artifact_paths()
    sample = store.con.execute(
        "SELECT s.name, a.rel_path FROM symbol s JOIN artifact a USING(artifact_id)"
        " WHERE s.kind IN ('class','function') AND (s.name LIKE '%\\_%' ESCAPE '\\'"
        "   OR s.name GLOB '*[A-Z]*') LIMIT 40").fetchall()
    for r in sample:
        d = decide(index, f"{r['name']} in {r['rel_path']}")
        if d.outcome.startswith("EXPOSE"):
            bad = {canonical_path(h.rel_path) for h in d.hits} - {canonical_path(r["rel_path"])}
            if bad:
                v.append(f"I-2 scoped query on {r['rel_path']} exposed evidence from {sorted(bad)[:2]}")
                break

    # I-3 identity: the same query twice must give the same decision, and a
    # multi-candidate subject must never resolve by ordering.
    for r in sample[:20]:
        a, b = decide(index, r["name"]), decide(index, r["name"])
        if a.outcome != b.outcome:
            v.append(f"I-3 non-deterministic decision for {r['name']!r}")
            break
        if a.outcome.startswith("EXPOSE"):
            n = len({s["qualified_name"] for s in index.find_symbols(r["name"])})
            if n > 1:
                v.append(f"I-3 {r['name']!r} resolves to {n} symbols yet exposed "
                         f"— identity decided by ordering")
                break
    return v


def main():
    for corpus in ("eval/corpus2", "eval/corpus3"):
        db = tempfile.mktemp(suffix=".db")
        store = Store(db); ingest(store, ROOT / corpus)
        violations = check(store)
        print(f"{corpus}: {'OK — I-1, I-2, I-3 hold' if not violations else 'VIOLATIONS'}")
        for x in violations:
            print("   ", x)
        store.close()


if __name__ == "__main__":
    main()
