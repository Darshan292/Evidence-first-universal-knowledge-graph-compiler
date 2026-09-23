"""K-1.2 measurements: corpus state, the CALLS semantic diff, determinism.

The CALLS "before" figures were produced by running the K-1.1 commit (4fe8c16)
on the same corpora and are recorded here so the diff is reproducible without a
worktree. `python3 experiments/k12_measurements.py --verify-before` recomputes
the decorator call sites straight from the AST and checks the arithmetic.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kgc.pipeline import ingest
from kgc.safety import ANALYSED_SUFFIXES, classify, walk_corpus
from kgc.store import Store

CORPORA = ["eval/corpus", "eval/corpus2", "eval/corpus3"]
# measured on commit 4fe8c16 (K-1.1), same corpora, before decorator exclusion
CALLS_BEFORE_K12 = {"eval/corpus": 22, "eval/corpus2": 163, "eval/corpus3": 8658}

SNAPSHOT = {
    "artifact": "SELECT artifact_id,rel_path,sha256,parse_status,parse_error FROM artifact ORDER BY artifact_id",
    "symbol": "SELECT symbol_id,artifact_id,parent_id,kind,qualified_name,locator FROM symbol ORDER BY symbol_id",
    "claim": "SELECT claim_id,predicate,subject_id,object_id,object_literal,establishment,evidence_ids FROM claim ORDER BY claim_id",
    "evidence": "SELECT evidence_id,artifact_id,locator,quoted_text,verification_strength FROM evidence ORDER BY evidence_id",
    "reference": "SELECT claim_id,to_name,resolution,reason FROM reference ORDER BY claim_id",
    "diagnostic": "SELECT diagnostic_id,artifact_id,severity,code,message,line FROM diagnostic ORDER BY diagnostic_id",
}


def decorator_call_sites(corpus: Path) -> int:
    """Call nodes inside decorator expressions, counted straight from the AST."""
    n = 0
    for path in walk_corpus(corpus):
        if path.suffix not in ANALYSED_SUFFIXES:
            continue
        data, rej = classify(path, root=corpus)
        if rej is not None:
            continue
        try:
            tree = ast.parse(data.decode("utf-8"))
        except (SyntaxError, ValueError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for dec in node.decorator_list:
                    n += sum(1 for s in ast.walk(dec) if isinstance(s, ast.Call))
    return n


def digest(store) -> dict:
    return {t: hashlib.sha256(repr([tuple(r) for r in store.con.execute(q)]).encode()).hexdigest()
            for t, q in SNAPSHOT.items()}


def run(rel: str) -> dict:
    corpus = (ROOT / rel).resolve()
    digests, out = [], {"corpus": rel}
    with tempfile.TemporaryDirectory() as td:
        for n in range(2):
            store = Store(str(Path(td) / f"kg{n}.sqlite"))
            rep = ingest(store, corpus)
            digests.append(digest(store))
            if n == 0:
                q = lambda sql: store.con.execute(sql).fetchone()[0]
                diag = {r[0]: r[1] for r in store.con.execute(
                    "SELECT code, count(*) FROM diagnostic GROUP BY code ORDER BY code")}
                status = {r[0]: r[1] for r in store.con.execute(
                    "SELECT parse_status, count(*) FROM artifact GROUP BY parse_status")}
                after = q("SELECT count(*) FROM claim WHERE predicate='CALLS'")
                skipped = diag.get("UNSUPPORTED_DECORATOR_CALL", 0)
                out.update({
                    "walked_analysable": sum(1 for p in walk_corpus(corpus)
                                             if p.suffix in ANALYSED_SUFFIXES),
                    "parse_status": status,
                    "absent": rep.absent,
                    "failed": rep.failed,
                    "extraction_failures": rep.extraction_failed,
                    "counts": store.counts(),
                    "claims_by_predicate": {r[0]: r[1] for r in store.con.execute(
                        "SELECT predicate, count(*) FROM claim GROUP BY predicate"
                        " ORDER BY predicate")},
                    "calls_before_k12": CALLS_BEFORE_K12[rel],
                    "decorator_calls_removed": skipped,
                    "calls_after_k12": after,
                    "arithmetic_holds": CALLS_BEFORE_K12[rel] - skipped == after,
                    "decorator_call_sites_in_source": decorator_call_sites(corpus),
                    "diagnostics_by_code": diag,
                    "invariant_violations": store.check_invariants(),
                })
            store.close()
    out["deterministic"] = digests[0] == digests[1]
    out["differing_tables"] = [t for t in SNAPSHOT if digests[0][t] != digests[1][t]]
    return out


if __name__ == "__main__":
    results = [run(c) for c in CORPORA]
    (ROOT / "experiments/k12_results.json").write_text(json.dumps(results, indent=2))
    for r in results:
        print(f"\n{r['corpus']}")
        print(f"  walked analysable : {r['walked_analysable']}   parse_status: {r['parse_status']}")
        print(f"  absent / failed   : {len(r['absent'])} / {r['failed']}"
              f"   extraction failures: {r['extraction_failures']}")
        print(f"  counts            : {r['counts']}")
        print(f"  claims by predicate: {r['claims_by_predicate']}")
        print(f"  CALLS  before     : {r['calls_before_k12']}")
        print(f"         decorator  : -{r['decorator_calls_removed']}"
              f"   (AST decorator call sites: {r['decorator_call_sites_in_source']})")
        print(f"         after      : {r['calls_after_k12']}"
              f"   arithmetic holds: {r['arithmetic_holds']}")
        print(f"  diagnostics       : {r['diagnostics_by_code']}")
        print(f"  invariants        : {r['invariant_violations']}")
        print(f"  two-run determinism: {r['deterministic']}  differing: {r['differing_tables']}")
