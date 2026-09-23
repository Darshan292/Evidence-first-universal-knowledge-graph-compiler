"""K-1 census: exactly how much of a corpus HAS_VALUE actually covers.

The capability is deterministic extraction of DIRECT class-body literals. This
counts what that reaches and, more importantly, what it does not, so the number
cannot be mistaken for "the compiler understands Python values".

Everything here is counted from the same AST the backend walks, not estimated.
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kgc.analysis import python_backend
from kgc.pipeline import ingest
from kgc.safety import ANALYSED_SUFFIXES, walk_corpus
from kgc.store import Store

CORPORA = ["eval/corpus2", "eval/corpus3"]


def census_source(text: str) -> Counter:
    """Classify every assignment by the context it sits in."""
    c = Counter()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        c["file_unparseable"] += 1
        return c

    # every context in which an assignment can appear, walked explicitly so a
    # nested `if` inside a class body is never counted as a class body
    def visit(node, in_class_body: bool, in_function: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Assign, ast.AnnAssign)):
                if in_class_body:
                    c["class_body_direct"] += 1
                    target, why = python_backend._class_literal(child)
                    if target is None:            # `why` is the refusal reason
                        c["class_body_refused"] += 1
                        c[f"refused::{why.split(',')[0]}"] += 1
                    else:
                        c["class_body_supported"] += 1
                elif in_function:
                    c["function_body"] += 1
                else:
                    c["module_or_nested"] += 1
                visit(child, False, in_function)
            elif isinstance(child, ast.ClassDef):
                visit(child, True, False)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, False, True)
            else:
                # an `if`/`try`/`with` inside a class body is NOT the class body
                visit(child, False, in_function)
    visit(tree, False, False)
    return c


def run(rel: str) -> dict:
    corpus = ROOT / rel
    totals = Counter()
    for path in walk_corpus(corpus):
        if path.suffix not in ANALYSED_SUFFIXES:
            continue
        try:
            totals += census_source(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, ValueError):
            totals["file_unreadable"] += 1

    with tempfile.TemporaryDirectory() as td:
        store = Store(str(Path(td) / "kg.sqlite"))
        rep = ingest(store, corpus)
        q = lambda s, *a: store.con.execute(s, a).fetchone()[0]
        emitted = q("SELECT count(*) FROM claim WHERE predicate='HAS_VALUE'")
        verified = q("SELECT count(*) FROM claim cl JOIN claim_evidence ce USING(claim_id)"
                     " JOIN evidence e USING(evidence_id) WHERE cl.predicate='HAS_VALUE'"
                     " AND e.verification_strength IN ('EXACT','REPRODUCIBLE')")
        diags = q("SELECT count(*) FROM diagnostic WHERE code='UNSUPPORTED_CLASS_LITERAL'")
        conflicted = [dict(r) for r in store.con.execute(
            "SELECT s.qualified_name qn, count(DISTINCT cl.object_literal) n"
            "  FROM claim cl JOIN symbol s ON s.symbol_id=cl.subject_id"
            " WHERE cl.predicate='HAS_VALUE' GROUP BY cl.subject_id HAVING n>1")]
        est = sorted({r[0] for r in store.con.execute(
            "SELECT DISTINCT establishment FROM claim WHERE predicate='HAS_VALUE'")})
        violations = store.check_invariants()
        # An artifact whose extract transaction rolled back contributes nothing
        # to the graph, so its literals are counted in the source but absent
        # from the claims. This is pre-existing (K1_REPORT.md §9) and must not
        # be quietly absorbed into the coverage number.
        lost = [r[0] for r in store.con.execute(
            "SELECT target FROM work_item WHERE state='FAILED' AND stage='extract'")]
        store.close()

    return {
        "corpus": rel, "artifacts": rep.seen,
        "class_body_assignments": totals["class_body_direct"],
        "supported": totals["class_body_supported"],
        "refused": totals["class_body_refused"],
        "refusal_reasons": {k.split("::", 1)[1]: v for k, v in sorted(totals.items())
                            if k.startswith("refused::")},
        "has_value_claims": emitted,
        "evidence_verified": verified,
        "evidence_verification_rate": round(verified / emitted, 4) if emitted else None,
        "diagnostics_recorded": diags,
        "establishment": est,
        "subjects_with_several_values": conflicted,
        "out_of_scope_module_level_or_nested": totals["module_or_nested"],
        "out_of_scope_function_body": totals["function_body"],
        "invariant_violations": violations,
        "artifacts_lost_before_claims": lost,
        "supported_minus_emitted": totals["class_body_supported"] - emitted,
    }


if __name__ == "__main__":
    out = [run(c) for c in CORPORA]
    (ROOT / "experiments/k1_census_results.json").write_text(json.dumps(out, indent=2))
    for r in out:
        print(f"\n{r['corpus']}  ({r['artifacts']} artifacts)")
        print(f"  direct class-body assignments : {r['class_body_assignments']}")
        print(f"    supported (HAS_VALUE)       : {r['supported']}")
        print(f"    refused with a diagnostic   : {r['refused']}")
        for k, v in r["refusal_reasons"].items():
            print(f"        {v:5d}  {k}")
        print(f"  HAS_VALUE claims in the graph : {r['has_value_claims']}")
        print(f"  evidence verification rate    : {r['evidence_verification_rate']}"
              f"  ({r['evidence_verified']}/{r['has_value_claims']}, {r['establishment']})")
        print(f"  diagnostics recorded          : {r['diagnostics_recorded']}")
        print(f"  subjects with >1 value        : {r['subjects_with_several_values']}")
        print(f"  NOT reached (module/nested)   : {r['out_of_scope_module_level_or_nested']}")
        print(f"  NOT reached (function bodies) : {r['out_of_scope_function_body']}")
        print(f"  invariant violations          : {r['invariant_violations']}")
        if r["supported_minus_emitted"]:
            print(f"  supported but NOT in the graph: {r['supported_minus_emitted']}"
                  f"  (artifacts lost before claims: {r['artifacts_lost_before_claims']})")
