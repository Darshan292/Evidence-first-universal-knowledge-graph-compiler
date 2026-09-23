"""Item 11: independently verify a SAMPLE of compiled claims against raw source.

A deterministically generated graph is not ground truth merely because it was
generated deterministically. This re-derives the facts with a separate stdlib
procedure and compares.
"""
from __future__ import annotations
import ast, json, random, sys, tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kgc.pipeline import ingest
from kgc.store import Store

CORPUS = ROOT / "eval/corpus3"
random.seed(4242)
SAMPLE = 400


def independent_facts(corpus: Path):
    """Separate procedure: (file, kind, name, lineno) and base classes."""
    defs, bases = set(), set()
    for p in sorted(corpus.rglob("*.py")):
        rel = str(p.relative_to(corpus))
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.add((rel, "function", n.name))
            elif isinstance(n, ast.ClassDef):
                defs.add((rel, "class", n.name))
                for b in n.bases:
                    # dotted bases (`class X(r.BaseConverter)`) are as real as bare
                    # ones; recording only ast.Name made the checker, not the
                    # extractor, look wrong.
                    if isinstance(b, ast.Name):
                        bases.add((rel, n.name, b.id))
                    elif isinstance(b, ast.Attribute):
                        bases.add((rel, n.name, b.attr))
                        try:
                            bases.add((rel, n.name, ast.unparse(b)))
                        except Exception:
                            pass
    return defs, bases


def main():
    db = tempfile.mktemp(suffix=".db")
    store = Store(db); ingest(store, CORPUS)
    defs, bases = independent_facts(CORPUS)

    # --- sample CONTAINS claims (symbol definitions) ---
    rows = [dict(r) for r in store.con.execute("""
        SELECT cl.claim_id, cl.predicate, cl.establishment,
               s2.name AS obj_name, s2.kind AS obj_kind,
               a.rel_path, e.quoted_text, e.verification_strength, e.locator
          FROM claim cl
          JOIN symbol s2 ON s2.symbol_id = cl.object_id
          JOIN claim_evidence ce ON ce.claim_id = cl.claim_id
          JOIN evidence e ON e.evidence_id = ce.evidence_id
          JOIN artifact a ON a.artifact_id = e.artifact_id
         WHERE cl.predicate='CONTAINS' AND s2.kind IN ('function','method','class')""")]
    sample = random.sample(rows, min(SAMPLE, len(rows)))

    ok, wrong, ev_ok, ev_bad = 0, [], 0, []
    for r in sample:
        kind = "function" if r["obj_kind"] in ("function", "method") else "class"
        if (r["rel_path"], kind, r["obj_name"]) in defs:
            ok += 1
        else:
            wrong.append({k: r[k] for k in ("rel_path", "obj_name", "obj_kind")})
        # evidence: re-read the artifact and compare the cited span byte-for-byte
        loc = json.loads(r["locator"])
        data = (CORPUS / r["rel_path"]).read_bytes()
        actual = data[loc["byte_start"]:loc["byte_end"]].decode("utf-8", errors="replace")
        if actual == r["quoted_text"]:
            ev_ok += 1
        else:
            ev_bad.append(r["rel_path"])

    # --- sample EXTENDS claims ---
    ext = [dict(r) for r in store.con.execute("""
        SELECT s1.name AS child, COALESCE(s2.name, cl.object_literal) AS base, a.rel_path
          FROM claim cl
          JOIN symbol s1 ON s1.symbol_id = cl.subject_id
          LEFT JOIN symbol s2 ON s2.symbol_id = cl.object_id
          JOIN claim_evidence ce ON ce.claim_id = cl.claim_id
          JOIN evidence e ON e.evidence_id = ce.evidence_id
          JOIN artifact a ON a.artifact_id = e.artifact_id
         WHERE cl.predicate='EXTENDS'""")]
    ext_sample = random.sample(ext, min(150, len(ext)))
    ext_ok = sum(1 for r in ext_sample
                 if (r["rel_path"], r["child"], (r["base"] or "").split(".")[-1]) in bases)
    ext_wrong = [r for r in ext_sample
                 if (r["rel_path"], r["child"], (r["base"] or "").split(".")[-1]) not in bases]

    total_claims = store.counts()["claim"]
    unverifiable = store.con.execute(
        "SELECT count(*) n FROM claim cl JOIN claim_evidence ce USING(claim_id)"
        " JOIN evidence e USING(evidence_id)"
        " WHERE e.verification_strength NOT IN ('EXACT','REPRODUCIBLE')").fetchone()["n"]

    out = {
        "corpus": "corpus3 (werkzeug)", "total_claims": total_claims,
        "definition_claims": {
            "population": len(rows), "sample_size": len(sample),
            "independently_confirmed": ok,
            "deterministic_precision": round(ok / max(len(sample), 1), 4),
            "false_deterministic_rate": round(len(wrong) / max(len(sample), 1), 4),
            "disagreements": wrong[:5],
        },
        "evidence_spans": {
            "sample_size": len(sample), "byte_exact": ev_ok,
            "evidence_correctness": round(ev_ok / max(len(sample), 1), 4),
            "mismatches": ev_bad[:5],
        },
        "extends_claims": {
            "population": len(ext), "sample_size": len(ext_sample),
            "independently_confirmed": ext_ok,
            "precision": round(ext_ok / max(len(ext_sample), 1), 4),
            "disagreements": [{k: r[k] for k in ("rel_path", "child", "base")}
                              for r in ext_wrong[:5]],
        },
        "claims_without_verifiable_evidence": unverifiable,
    }
    print(json.dumps(out, indent=2))
    (ROOT / "experiments/independent_verification_results.json").write_text(json.dumps(out, indent=2))
    store.close()


if __name__ == "__main__":
    main()
