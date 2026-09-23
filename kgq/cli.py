"""Demo 0.1 command line.

    python3 -m kgq.cli compile --root eval/corpus3 --db eval/j1/demo.sqlite
    python3 -m kgq.cli ask "How does send_from_directory prevent unsafe paths?" \
        --db eval/j1/demo.sqlite --root eval/corpus3

`ask` works with no model configured: it shows the retrieved evidence and the
deterministic structural facts and abstains from the semantic part. That is the
point -- the deterministic half needs no API key.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path

from kgq import DEMO_VERSION
from kgq.answer import ABSTAIN_AMBIGUOUS, ANSWER, Budget, ask
from kgq.provider import Provider, ProviderError
from kgq.validate import EXPLICIT_CONTRADICTION, NOT_ESTABLISHED, SUPPORTED

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, YELLOW, RED, BLUE = "\033[32m", "\033[33m", "\033[31m", "\033[36m"


def _c(s, colour, on):
    return f"{colour}{s}{RESET}" if on else s


def render_text(r, colour=True, max_excerpt=14) -> str:
    out = []
    a = out.append
    a(_c("QUESTION", BOLD, colour) + f"  {r.question}")
    a("")

    if r.status == ANSWER:
        a(_c("ANSWER", BOLD, colour) + _c("   [PROPOSED — semantic interpretation]", YELLOW, colour))
        a("")
        for line in r.answer.splitlines():
            a("  " + line)
        a("")
        a(_c("STATEMENTS AND THEIR EVIDENCE", BOLD, colour))
        for c in r.validation.get("claims", []):
            mark = _c("✔", GREEN, colour) if c["ok"] else _c("✘", RED, colour)
            a(f"  {mark} {c['text']}")
            for e in c["evidence"]:
                if e["ok"]:
                    a(_c(f"      evidence {e['evidence_id'][:12]}  {e['rel_path']}"
                         f"  bytes {e['byte_start']}-{e['byte_end']}", DIM, colour))
                else:
                    a(_c(f"      REJECTED {e['evidence_id'][:12]}: {e['error']}", RED, colour))
            for s in c["structural"]:
                icon = {SUPPORTED: _c("supported by graph", GREEN, colour),
                        EXPLICIT_CONTRADICTION: _c("CONTRADICTED BY GRAPH", RED, colour),
                        NOT_ESTABLISHED: _c("not established", DIM, colour)}[s["status"]]
                a(f"      {s['predicate']}({s['subject']}, {s['object']}) — {icon}")
                a(_c(f"        {s['detail']}", DIM, colour))
    else:
        label = ("AMBIGUOUS — REFUSED" if r.status == ABSTAIN_AMBIGUOUS else "ABSTAINED")
        a(_c(label, BOLD, colour) + _c("   no answer was shown", YELLOW, colour))
        a("")
        a("  " + r.abstain_reason)

    if r.identity.get("names"):
        a("")
        a(_c("SUBJECT IDENTITY", BOLD, colour) + _c("  (deterministic — the model never chooses)", DIM, colour))
        for n in r.identity["names"]:
            if n["resolved"]:
                a(f"  {n['name']} -> {n['resolved']}")
            else:
                a(_c(f"  {n['name']} -> AMBIGUOUS across {len(n['candidates'])}: "
                     f"{', '.join(n['candidates'][:4])}", YELLOW, colour))
        if r.identity.get("scope"):
            a(_c(f"  scope: {r.identity['scope']}", DIM, colour))

    a("")
    a(_c("EVIDENCE RETRIEVED", BOLD, colour) + _c("  (deterministic, no model)", DIM, colour))
    for e in r.evidence:
        a(f"  {e['evidence_id'][:12]}  {e['rel_path']}:{e['line_start']}  "
          f"{e['symbol']}  {e['chars']}B")
        a(_c(f"      found by: {e['how']}", DIM, colour))
        for line in (e.get("excerpt") or "").splitlines()[:max_excerpt]:
            a(_c("      │ " + line, DIM, colour))
        if len((e.get("excerpt") or "").splitlines()) > max_excerpt:
            a(_c("      │ ...", DIM, colour))

    if r.structural_facts:
        a("")
        a(_c("DETERMINISTIC STRUCTURAL FACTS", BOLD, colour)
          + _c("  [DERIVED — compiler, not model]", GREEN, colour))
        for f in r.structural_facts[:14]:
            res = "" if f["resolved"] else _c("  (target unresolved)", DIM, colour)
            a(f"  {f['predicate']}({f['subject'].split('.')[-1]}, {f['object']}){res}")
            a(_c(f"      {f['rel_path']} bytes {f['byte_start']}-{f['byte_end']}: "
                 f"{f['excerpt'].strip()[:70]}", DIM, colour))

    a("")
    a(_c("WHY THIS OUTCOME", BOLD, colour))
    a("  " + (r.validation.get("reason") or r.abstain_reason or "—"))
    a("")
    a(_c("RUN", BOLD, colour) + f"  status={r.status}  establishment={r.establishment}  "
      f"attempts={r.attempts}  regenerations={r.regenerations}  "
      f"structural={r.structural_status or '—'}")
    a(f"  {r.usage}  latency={r.latency_seconds}s  budget={r.budget}")
    return "\n".join(out)


def render_html(r) -> str:
    e = html.escape
    def block(title, body, cls=""):
        return f'<section class="{cls}"><h2>{e(title)}</h2>{body}</section>'
    ev = "".join(
        f'<article><header><code>{e(x["evidence_id"][:12])}</code> '
        f'<b>{e(x["rel_path"])}</b>:{x["line_start"]} — {e(x["symbol"])} '
        f'<span class="dim">({x["chars"]}B, {e(x["how"])})</span></header>'
        f'<pre>{e(x.get("excerpt") or "")}</pre></article>' for x in r.evidence)
    facts = "".join(
        f'<li><code>{e(f["predicate"])}({e(f["subject"].split(".")[-1])}, {e(f["object"])})</code>'
        f'<span class="dim"> — {e(f["rel_path"])} bytes {f["byte_start"]}-{f["byte_end"]}</span>'
        f'<pre>{e(f["excerpt"].strip()[:200])}</pre></li>' for f in r.structural_facts[:12])
    stmts = "".join(
        f'<li class="{"ok" if c["ok"] else "bad"}">{e(c["text"])}'
        + "".join(f'<div class="dim">evidence {e(x["evidence_id"][:12])} — '
                  f'{e(x["rel_path"])} bytes {x["byte_start"]}-{x["byte_end"]}</div>'
                  for x in c["evidence"])
        + "".join(f'<div class="struct {s["status"].lower()}">'
                  f'{e(s["predicate"])}({e(s["subject"])}, {e(s["object"])}) — {e(s["status"])}'
                  f'<span class="dim"> {e(s["detail"])}</span></div>' for s in c["structural"])
        + "</li>" for c in r.validation.get("claims", []))
    head = (f'<p class="verdict {"answer" if r.status == ANSWER else "abstain"}">'
            f'{e(r.status)} — {e(r.establishment) if r.status == ANSWER else e(r.abstain_reason)}</p>')
    return f"""<!doctype html><meta charset="utf-8"><title>Evidence-first answer</title>
<style>
 :root{{--fg:#1b1f23;--bg:#fff;--dim:#6a737d;--line:#e1e4e8}}
 @media(prefers-color-scheme:dark){{:root{{--fg:#e6edf3;--bg:#0d1117;--dim:#8b949e;--line:#30363d}}}}
 body{{font:15px/1.6 ui-sans-serif,system-ui,sans-serif;color:var(--fg);background:var(--bg);
       max-width:56rem;margin:2rem auto;padding:0 16px}}
 h1{{font-size:1.4rem}} h2{{font-size:.95rem;text-transform:uppercase;letter-spacing:.06em;
   color:var(--dim);border-bottom:1px solid var(--line);padding-bottom:.3rem;margin-top:2rem}}
 pre{{background:color-mix(in srgb,var(--fg) 5%,transparent);padding:.6rem;border-radius:6px;
   overflow:auto;font-size:12px;line-height:1.45}}
 .dim{{color:var(--dim);font-size:12.5px}} li{{margin:.6rem 0}}
 .ok{{border-left:3px solid #2da44e;padding-left:.7rem}}
 .bad{{border-left:3px solid #cf222e;padding-left:.7rem}}
 .verdict{{font-weight:600}} .answer{{color:#2da44e}} .abstain{{color:#bf8700}}
 .struct{{font-size:12.5px;margin-top:.2rem}} .explicit_contradiction{{color:#cf222e}}
 .supported{{color:#2da44e}} ul{{padding-left:1.1rem}}
</style>
<h1>{e(r.question)}</h1>{head}
{block("Answer (PROPOSED — semantic interpretation)", "<p>" + e(r.answer or "—") + "</p>") if r.status == ANSWER else ""}
{block("Statements and their evidence", "<ul>" + stmts + "</ul>") if stmts else ""}
{block("Evidence retrieved (deterministic)", ev)}
{block("Deterministic structural facts (DERIVED — compiler, not model)", "<ul>" + facts + "</ul>") if facts else ""}
{block("Why this outcome", "<p>" + e(r.validation.get("reason") or r.abstain_reason or "—") + "</p>")}
{block("Run", f'<pre>{e(json.dumps({"status": r.status, "attempts": r.attempts, "regenerations": r.regenerations, "structural": r.structural_status, "usage": r.usage, "budget": r.budget, "latency_seconds": r.latency_seconds}, indent=2))}</pre>')}
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kgq", description=f"Demo {DEMO_VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compile", help="build a fresh graph from a source tree")
    c.add_argument("--root", required=True); c.add_argument("--db", required=True)

    a = sub.add_parser("ask", help="ask a natural-language question")
    a.add_argument("question")
    a.add_argument("--db", required=True)
    a.add_argument("--root", required=True)
    a.add_argument("--json", action="store_true")
    a.add_argument("--html", metavar="PATH")
    a.add_argument("--no-llm", action="store_true",
                   help="deterministic half only: retrieval and structural facts")
    a.add_argument("--cache", default="eval/j1/kgq_cache.sqlite")
    a.add_argument("--max-context-chars", type=int, default=24000)
    a.add_argument("--answer-attempts", type=int, default=2)
    a.add_argument("--no-colour", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "compile":
        from kgc.pipeline import ingest
        from kgc.store import Store
        db = Path(args.db)
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db) + suffix)
            if p.exists():
                p.unlink()
        db.parent.mkdir(parents=True, exist_ok=True)
        store = Store(str(db))
        rep = ingest(store, Path(args.root).resolve())
        print(json.dumps(rep.as_dict(), indent=2))
        if rep.absent:
            print("COVERAGE: walked files with no artifact row", file=sys.stderr)
            return 1
        return 0

    provider = None
    if not args.no_llm:
        try:
            provider = Provider.from_env(cache_path=args.cache)
        except ProviderError as e:
            print(f"note: {e}\n", file=sys.stderr)

    r = ask(args.question, db_path=args.db, corpus_root=args.root, provider=provider,
            budget=Budget(max_context_chars=args.max_context_chars,
                          answer_attempts=args.answer_attempts))
    if args.html:
        Path(args.html).write_text(render_html(r), encoding="utf-8")
    if args.json:
        print(json.dumps(r.as_dict(), indent=2))
    else:
        print(render_text(r, colour=not args.no_colour and sys.stdout.isatty()))
    return 0 if r.status == ANSWER else 2


if __name__ == "__main__":
    raise SystemExit(main())
