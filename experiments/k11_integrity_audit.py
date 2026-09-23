"""K-1.1 audit: artifact coverage, occurrence identity, determinism.

Reconciles the filesystem walk against persisted artifacts, then re-derives
parent and reference-subject correctness from the source independently of the
mapper that produced them.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kgc.pipeline import ingest
from kgc.safety import ANALYSED_SUFFIXES, walk_corpus
from kgc.store import Store

CORPORA = ["eval/corpus", "eval/corpus2", "eval/corpus3"]
PREVIOUSLY_LOST = [
    "src/werkzeug/local.py", "src/werkzeug/test.py", "src/werkzeug/testapp.py",
    "src/werkzeug/debug/__init__.py", "tests/test_test.py", "tests/test_utils.py",
    "tests/middleware/test_proxy_fix.py",
]

SNAPSHOT = {
    "artifact": "SELECT artifact_id,rel_path,sha256,parse_status,parse_error FROM artifact ORDER BY artifact_id",
    "symbol": "SELECT symbol_id,artifact_id,parent_id,kind,qualified_name,locator FROM symbol ORDER BY symbol_id",
    "claim": "SELECT claim_id,predicate,subject_id,object_id,object_literal,establishment,evidence_ids FROM claim ORDER BY claim_id",
    "evidence": "SELECT evidence_id,artifact_id,locator,quoted_text,verification_strength FROM evidence ORDER BY evidence_id",
    "reference": "SELECT claim_id,to_name,resolution,reason FROM reference ORDER BY claim_id",
    "diagnostic": "SELECT diagnostic_id,artifact_id,severity,code,message,line FROM diagnostic ORDER BY diagnostic_id",
}


def digest(store) -> dict:
    return {t: hashlib.sha256(repr([tuple(r) for r in store.con.execute(q)]).encode()).hexdigest()
            for t, q in SNAPSHOT.items()}


def identity_audit(store) -> dict:
    """Re-derive parent and subject correctness from persisted spans."""
    bad_parent = orphan = 0
    ids = {}
    for r in store.con.execute("SELECT symbol_id,artifact_id,qualified_name,locator,parent_id,kind"
                               "  FROM symbol"):
        p = json.loads(r["locator"])
        ids[r["symbol_id"]] = (r["artifact_id"], r["qualified_name"],
                               p["byte_start"], p["byte_end"], r["parent_id"], r["kind"])
    occ = defaultdict(int)
    for aid, qn, _b0, _b1, _p, _k in ids.values():
        occ[(aid, qn)] += 1

    for sid, (aid, qn, b0, b1, pid, kind) in ids.items():
        if pid is None:
            if kind != "module":
                orphan += 1
            continue
        paid, pqn, p0, p1, _pp, _pk = ids[pid]
        if paid != aid or not qn.startswith(pqn + ".") or not (p0 <= b0 and b1 <= p1):
            bad_parent += 1

    inside = outside_unique = outside_ambiguous = 0
    for r in store.con.execute(
            "SELECT cl.subject_id sid, e.locator el FROM claim cl"
            "  JOIN claim_evidence ce ON ce.claim_id=cl.claim_id"
            "  JOIN evidence e ON e.evidence_id=ce.evidence_id"):
        aid, qn, b0, b1, _p, _k = ids[r["sid"]]
        el = json.loads(r["el"])
        if b0 <= el["byte_start"] and el["byte_end"] <= b1:
            inside += 1
        elif occ[(aid, qn)] == 1:
            outside_unique += 1
        else:
            outside_ambiguous += 1

    return {
        "symbols": len(ids),
        "duplicate_qualified_names": sum(1 for v in occ.values() if v > 1),
        "symbols_sharing_a_qualified_name": sum(v for v in occ.values() if v > 1),
        "parent_links_violating_containment": bad_parent,
        "non_module_symbols_without_a_parent": orphan,
        "claims_with_evidence_inside_the_subject": inside,
        "claims_outside_but_subject_name_unique": outside_unique,
        "claims_outside_AND_subject_ambiguous": outside_ambiguous,
    }


def run(rel: str) -> dict:
    corpus = (ROOT / rel).resolve()
    walked = sorted(str(p.relative_to(corpus)) for p in walk_corpus(corpus)
                    if p.suffix in ANALYSED_SUFFIXES)
    digests = []
    out: dict = {"corpus": rel}
    with tempfile.TemporaryDirectory() as td:
        for n in range(2):                       # same corpus, two databases
            store = Store(str(Path(td) / f"kg{n}.sqlite"))
            rep = ingest(store, corpus)
            digests.append(digest(store))
            if n == 0:
                status = {r[0]: r[1] for r in store.con.execute(
                    "SELECT parse_status, count(*) FROM artifact GROUP BY parse_status")}
                stored = {r[0] for r in store.con.execute("SELECT rel_path FROM artifact")}
                out.update({
                    "walked_analysable": len(walked),
                    "OK": status.get("OK", 0), "FAILED": status.get("FAILED", 0),
                    "SKIPPED": status.get("SKIPPED", 0),
                    "UNSUPPORTED": status.get("UNSUPPORTED", 0),
                    "absent": sorted(set(walked) - stored),
                    "report_absent": rep.absent,
                    "extraction_failures": rep.extraction_failed,
                    "counts": store.counts(),
                    "invariant_violations": store.check_invariants(),
                    "identity": identity_audit(store),
                    "failed_artifacts": [dict(r) for r in store.con.execute(
                        "SELECT rel_path, parse_error FROM artifact"
                        " WHERE parse_status='FAILED' ORDER BY rel_path")],
                })
                if rel == "eval/corpus3":
                    out["previously_lost"] = {
                        f: (store.con.execute("SELECT parse_status FROM artifact WHERE rel_path=?",
                                              (f,)).fetchone() or ["ABSENT"])[0]
                        for f in PREVIOUSLY_LOST}
            store.close()
    out["deterministic"] = digests[0] == digests[1]
    out["differing_tables"] = [t for t in SNAPSHOT if digests[0][t] != digests[1][t]]
    return out


if __name__ == "__main__":
    results = [run(c) for c in CORPORA]
    (ROOT / "experiments/k11_integrity_results.json").write_text(json.dumps(results, indent=2))
    for r in results:
        print(f"\n{r['corpus']}")
        print(f"  walked analysable : {r['walked_analysable']}")
        print(f"  OK / FAILED       : {r['OK']} / {r['FAILED']}")
        print(f"  SKIPPED / UNSUPP. : {r['SKIPPED']} / {r['UNSUPPORTED']}")
        print(f"  ABSENT            : {len(r['absent'])} {r['absent'][:5]}")
        print(f"  extraction failures: {r['extraction_failures']}   "
              f"failed artifacts: {r['failed_artifacts']}")
        print(f"  counts            : {r['counts']}")
        print(f"  invariants        : {r['invariant_violations']}")
        i = r["identity"]
        print(f"  duplicate qnames  : {i['duplicate_qualified_names']} "
              f"({i['symbols_sharing_a_qualified_name']} symbols)")
        print(f"  bad parent links  : {i['parent_links_violating_containment']}   "
              f"orphans: {i['non_module_symbols_without_a_parent']}")
        print(f"  subject attribution: inside {i['claims_with_evidence_inside_the_subject']}, "
              f"outside-but-unique {i['claims_outside_but_subject_name_unique']}, "
              f"AMBIGUOUS {i['claims_outside_AND_subject_ambiguous']}")
        print(f"  two-run determinism: {r['deterministic']}  differing: {r['differing_tables']}")
        if "previously_lost" in r:
            print(f"  previously lost   : {r['previously_lost']}")
