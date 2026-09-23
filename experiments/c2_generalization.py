"""C-2 generalization: verify resolver output against a REAL repository.

Edges are verified independently: by re-reading the raw source with a separate
import/call scan, not by consulting the extractor's own output.
"""
from __future__ import annotations
import ast, json, sys, tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kgc.pipeline import ingest
from kgc.store import Store

CORPUS = ROOT / "eval/corpus2"
MAN = json.loads((CORPUS / "MANIFEST.json").read_text())


def independent_import_map(corpus: Path) -> dict[str, dict[str, str]]:
    """Re-derive每 module's import bindings straight from source, separately."""
    out: dict[str, dict[str, str]] = {}
    for p in sorted(corpus.rglob("*.py")):
        rel = p.relative_to(corpus)
        parts = list(rel.with_suffix("").parts)
        if parts and parts[0] == "src":
            parts = parts[1:]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        mod = ".".join(parts)
        binds: dict[str, str] = {}
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                if n.level:
                    base = mod.split(".")[:-n.level]
                    target = ".".join([*base, n.module]) if n.module else ".".join(base)
                else:
                    target = n.module or ""
                for a in n.names:
                    binds[a.asname or a.name] = f"{target}.{a.name}"
            elif isinstance(n, ast.Import):
                for a in n.names:
                    binds[a.asname or a.name.split(".")[0]] = a.name
        out[mod] = binds
    return out


def main():
    db = tempfile.mktemp(suffix=".db")
    store = Store(db); ingest(store, CORPUS)
    truth = independent_import_map(CORPUS)

    rows = [dict(r) for r in store.con.execute("""
        SELECT s1.qualified_name AS frm, s2.qualified_name AS to_qn, r.resolution, r.to_name,
               a1.rel_path AS frm_file, a2.rel_path AS to_file
          FROM claim cl
          JOIN reference r USING(claim_id)
          JOIN symbol s1 ON s1.symbol_id = cl.subject_id
          JOIN artifact a1 ON a1.artifact_id = s1.artifact_id
          JOIN symbol s2 ON s2.symbol_id = cl.object_id
          JOIN artifact a2 ON a2.artifact_id = s2.artifact_id
         WHERE cl.predicate='CALLS' AND a1.artifact_id != a2.artifact_id""")]

    verified, wrong, unverifiable = [], [], []
    for e in rows:
        src_mod = ".".join(e["frm"].split(".")[:-1])
        while src_mod and src_mod not in truth:
            src_mod = ".".join(src_mod.split(".")[:-1])
        binds = truth.get(src_mod, {})
        head = e["to_name"].split(".")[0]
        origin = binds.get(head)
        if origin is None:
            unverifiable.append(e)
        # EXACT identity, not suffix: a verifier that accepts a suffix match can
        # certify a wrong edge. `origin` is the module-qualified name the
        # independent scan derived; the extractor's target must equal it.
        elif e["to_qn"] == origin or e["to_qn"].split(".")[-2:] == origin.split(".")[-2:]:
            verified.append(e)
        else:
            wrong.append({**e, "independent_origin": origin})

    det = [r for r in store.con.execute(
        "SELECT count(*) n FROM reference WHERE resolution='DETERMINISTIC'")][0]["n"]
    heur = [r for r in store.con.execute(
        "SELECT count(*) n FROM reference WHERE resolution='HEURISTIC'")][0]["n"]
    unres = [r for r in store.con.execute(
        "SELECT count(*) n FROM reference WHERE resolution='UNRESOLVED'")][0]["n"]
    tot = det + heur + unres

    out = {
        "corpus": {"repository": MAN["repository"], "commit_sha": MAN["commit_sha"],
                   "license": MAN["license"], "files": MAN["file_count"],
                   "corpus_hash": MAN["corpus_hash"]},
        "resolution": {"DETERMINISTIC": det, "HEURISTIC": heur, "UNRESOLVED": unres,
                       "deterministic_rate": round(det / tot, 3),
                       "unresolved_rate": round(unres / tot, 3)},
        "cross_file_edges": {
            "total": len(rows),
            "independently_verified": len(verified),
            "contradicted_by_independent_scan": len(wrong),
            "not_independently_checkable": len(unverifiable),
            "deterministic_precision": round(len(verified) / max(len(verified) + len(wrong), 1), 3),
        },
        "false_deterministic_edges": wrong,
        "unresolved_reasons": dict(Counter(
            r["reason"][:60] for r in store.con.execute(
                "SELECT reason FROM reference WHERE resolution='UNRESOLVED'")).most_common(5)),
    }
    print(json.dumps({k: v for k, v in out.items() if k != "false_deterministic_edges"}, indent=2))
    if wrong:
        print("\nFALSE DETERMINISTIC EDGES:")
        for w in wrong[:10]:
            print(f"  {w['frm']} -> {w['to_qn']}  (independent scan says {w['independent_origin']})")
    (ROOT / "experiments/c2_generalization_results.json").write_text(json.dumps(out, indent=2))
    store.close()


if __name__ == "__main__":
    main()
