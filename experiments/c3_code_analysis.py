"""C-3: what deterministic guarantees does each analysis approach provide?

Four categories from the gate: valid Python, cross-module, syntax errors,
unsupported language. Compares stdlib `ast` against Tree-sitter on the
categories where they can differ. Reads gold; never tunes against it.
"""
from __future__ import annotations
import ast, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOLD = json.loads((ROOT / "eval/malformed/gold.json").read_text())
MAL = ROOT / "eval/malformed"


def ast_symbols(src: str):
    try:
        t = ast.parse(src)
    except SyntaxError:
        return None, "SyntaxError"
    except Exception as e:
        return None, type(e).__name__
    return sorted(n.name for n in ast.walk(t)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))), None


def ts_symbols(src: bytes):
    import tree_sitter_python as tsp
    from tree_sitter import Language, Parser, Query, QueryCursor
    LANG = Language(tsp.language())
    tree = Parser(LANG).parse(src)
    q = Query(LANG, "(function_definition name:(identifier) @n)"
                    " (class_definition name:(identifier) @n)")
    caps = QueryCursor(q).captures(tree.root_node)
    names = sorted({src[n.start_byte:n.end_byte].decode() for ns in caps.values() for n in ns})
    return names, tree.root_node.has_error


def main():
    rows = []
    for case in GOLD["cases"]:
        src_b = (MAL / case["file"]).read_bytes()
        src = src_b.decode()
        gold = set(case["recoverable_symbols"])

        a_syms, a_err = ast_symbols(src)
        a_found = set(a_syms or [])
        t_syms, t_haserr = ts_symbols(src_b)
        t_found = set(t_syms)

        rows.append({
            "file": case["file"], "gold": sorted(gold),
            "ast_recovered": sorted(a_found), "ast_status": a_err or "OK",
            "ts_recovered": sorted(t_found), "ts_flags_error": bool(t_haserr),
            "ts_correct": sorted(t_found & gold),
            "ts_spurious": sorted(t_found - gold),
            "ts_missed": sorted(gold - t_found),
        })

    g = sum(len(r["gold"]) for r in rows)
    a = sum(len(r["ast_recovered"]) for r in rows)
    tc = sum(len(r["ts_correct"]) for r in rows)
    ts_all = sum(len(r["ts_recovered"]) for r in rows)
    sp = sum(len(r["ts_spurious"]) for r in rows)

    summary = {
        "gold_recoverable_symbols": g,
        "ast_recovered": a, "ast_recall": round(a / g, 3),
        "treesitter_recovered_total": ts_all,
        "treesitter_correct": tc, "treesitter_recall": round(tc / g, 3),
        "treesitter_spurious": sp,
        "treesitter_precision": round(tc / ts_all, 3) if ts_all else None,
        "treesitter_flags_error_on_all_malformed": all(r["ts_flags_error"] for r in rows),
    }
    print(json.dumps({"cases": rows, "summary": summary}, indent=2))
    (ROOT / "experiments/c3_results.json").write_text(
        json.dumps({"cases": rows, "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
