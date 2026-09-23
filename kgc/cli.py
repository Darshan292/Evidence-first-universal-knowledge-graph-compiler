"""Minimal CLI: ingest and inspect. Nothing else is authorized in Gate 1."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kgc import SOFTWARE_VERSION
from kgc.pipeline import ingest
from kgc.store import Store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kgc", description="evidence-first KG compiler (Gate 1)")
    ap.add_argument("--version", action="version", version=SOFTWARE_VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="compile a corpus into a SQLite database")
    p.add_argument("root"); p.add_argument("--db", required=True)
    p.add_argument("--no-resume", action="store_true")

    p = sub.add_parser("stats", help="row counts and coverage"); p.add_argument("--db", required=True)
    p = sub.add_parser("verify", help="run the independent invariant audit"); p.add_argument("--db", required=True)
    p = sub.add_parser("show", help="claims and evidence for a qualified name")
    p.add_argument("--db", required=True); p.add_argument("qualified_name")
    p = sub.add_parser("diagnostics", help="recorded parse failures and unresolved references")
    p.add_argument("--db", required=True); p.add_argument("--code")

    a = ap.parse_args(argv)
    store = Store(a.db)
    try:
        if a.cmd == "ingest":
            rep = ingest(store, Path(a.root), resume=not a.no_resume)
            print(json.dumps(rep.as_dict(), indent=2))
            if rep.absent:
                # A walked, analysable file with no artifact row is the failure
                # K-1.1 exists to prevent. It must not exit 0.
                for rel in rep.absent:
                    print(f"COVERAGE: {rel} was analysed but has no artifact row", file=sys.stderr)
                return 1
            return 0

        if a.cmd == "stats":
            counts = store.counts()
            cov = dict(store.con.execute(
                "SELECT parse_status, count(*) FROM artifact GROUP BY parse_status").fetchall())
            res = dict(store.con.execute(
                "SELECT resolution, count(*) FROM reference GROUP BY resolution").fetchall())
            total = sum(res.values()) or 1
            print(json.dumps({"counts": counts, "parse_status": cov, "resolution": res,
                              "deterministic_coverage": round(
                                  res.get("DETERMINISTIC", 0) / total, 4)}, indent=2))
            return 0

        if a.cmd == "verify":
            v = store.check_invariants()
            if not v:
                print("OK: all invariants hold"); return 0
            for x in v:
                print(f"VIOLATION: {x}", file=sys.stderr)
            return 1

        if a.cmd == "show":
            rows = store.con.execute(
                "SELECT symbol_id, kind, qualified_name FROM symbol WHERE qualified_name=?",
                (a.qualified_name,)).fetchall()
            if not rows:
                print(f"no symbol named {a.qualified_name!r}", file=sys.stderr); return 1
            for r in rows:
                print(f"{r['kind']} {r['qualified_name']}  [{r['symbol_id'][:12]}]")
                for c in store.claims_for_subject(r["symbol_id"]):
                    tgt = c["object_id"][:12] if c["object_id"] else (c["object_literal"] or "")
                    print(f"   {c['predicate']:<10} -> {tgt:<24} "
                          f"{c['establishment']}/{c['lifecycle']}")
                    for e in store.con.execute(
                        "SELECT e.* FROM evidence e JOIN claim_evidence ce USING(evidence_id)"
                        " WHERE ce.claim_id=?", (c["claim_id"],)):
                        loc = json.loads(e["locator"])
                        print(f"      evidence {e['verification_strength']} "
                              f"line {loc.get('line_start')}: {e['quoted_text'][:52]!r}")
            return 0

        if a.cmd == "diagnostics":
            q = "SELECT severity,code,message,line FROM diagnostic"
            args = ()
            if a.code:
                q += " WHERE code=?"; args = (a.code,)
            for r in store.con.execute(q + " ORDER BY code LIMIT 200", args):
                print(f"{r['severity']:<8} {r['code']:<22} line {r['line']}  {r['message'][:80]}")
            return 0
    finally:
        store.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
