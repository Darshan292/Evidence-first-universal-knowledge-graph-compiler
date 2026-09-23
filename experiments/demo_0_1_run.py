"""Demo 0.1 run: compile Werkzeug fresh, ask, record metrics.

The deterministic half runs for real. The model call cannot run in this
environment -- the network policy denies api.groq.com -- so the semantic step is
driven by a ScriptedProvider whose replies are built from the ids retrieval
actually returned. That simulates a WELL-BEHAVED model and a MISBEHAVING one;
it does not simulate a *correct* one, and the outputs say so.

    python3 experiments/demo_0_1_run.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kgc.pipeline import ingest
from kgc.store import Store
from kgq.answer import ANSWER, Budget, ask
from kgq.cli import render_html
from kgq.provider import ScriptedProvider
from kgq.retrieval import Retriever

CORPUS = ROOT / "eval/corpus3"
DB = ROOT / "eval/j1/demo_0_1.sqlite"

FLAGSHIP = "How does send_from_directory prevent unsafe paths?"

# the ten questions Demo 0.1 targets, taken from the frozen J-1 set
TARGET = [
    ("J1-009", "How does safe_join stop an untrusted path component from escaping "
               "the base directory, and what does it return when the path is rejected?"),
    ("J1-037", "How does LocalProxy resolve the object it forwards to, and what "
               "happens if nothing is bound?"),
    ("J1-047", "How does send_from_directory differ from send_file in terms of safety?"),
    ("J1-049", "Walk me through how run_simple wires together the reloader, the "
               "debugger and the server."),
    ("J1-054", "How does MapAdapter.build construct a URL, and what happens when a "
               "required argument is missing?"),
    ("J1-006", "What is the default status code and mimetype for a Response object?"),
    ("J1-022", "What is max_cookie_size and what happens when a Set-Cookie header "
               "goes over it?"),
    ("J1-002", "How many proxies does ProxyFix trust by default for each of the "
               "X-Forwarded- headers?"),
    ("J1-043", "What's the default cache timeout SharedDataMiddleware sends for "
               "static files?"),
    ("J1-038", "What are the default chunk_size and timeout for ProxyMiddleware?"),
]


def compile_fresh() -> dict:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB) + suffix)
        if p.exists():
            p.unlink()
    DB.parent.mkdir(parents=True, exist_ok=True)
    store = Store(str(DB))
    rep = ingest(store, CORPUS.resolve())
    counts = store.counts()
    violations = store.check_invariants()
    store.close()
    return {"absent": rep.absent, "failed": rep.failed, "symbols": rep.symbols,
            "claims": rep.claims, "counts": counts, "invariants": violations}


def well_behaved_reply(spans) -> str:
    """What a model that follows the contract would send back."""
    by = {s.symbol.split(".")[-1]: s for s in spans}
    claims = []
    if "send_from_directory" in by:
        claims.append({
            "text": "send_from_directory joins the untrusted path onto the directory "
                    "with safe_join and raises NotFound when the join is refused.",
            "evidence_ids": [by["send_from_directory"].evidence_id],
            "quote": "safe_join",
            "structural_dependencies": [
                {"predicate": "CALLS", "subject": "werkzeug.utils.send_from_directory",
                 "object": "werkzeug.security.safe_join"}]})
    if "safe_join" in by:
        claims.append({
            "text": "safe_join returns None when the untrusted component would escape "
                    "the base directory, which is the signal the caller acts on.",
            "evidence_ids": [by["safe_join"].evidence_id],
            "quote": "Return ``None`` if the path"})
    return json.dumps({
        "answer": "send_from_directory does not trust the path it is given. It passes the "
                  "directory and the untrusted path to safe_join, which returns None when "
                  "the result would escape the base directory; send_from_directory then "
                  "raises NotFound rather than opening the file.",
        "claims": claims})


def misbehaving_reply() -> str:
    """A model that invents a citation and a structural edge."""
    return json.dumps({
        "answer": "It validates the path using a built-in sandbox.",
        "claims": [{"text": "It uses a sandbox module.",
                    "evidence_ids": ["0" * 32],
                    "structural_dependencies": [
                        {"predicate": "CALLS", "subject": "werkzeug.utils.send_from_directory",
                         "object": "werkzeug.sandbox.check"}]}]})


def main() -> int:
    out: dict = {"corpus": "eval/corpus3 (Werkzeug 6048fa48)", "flagship": {}, "targets": []}
    out["compile"] = compile_fresh()

    r = Retriever(str(DB), str(CORPUS))
    spans, _ = r.retrieve(FLAGSHIP)
    r.close()

    # 1. a model that follows the contract
    good = ask(FLAGSHIP, db_path=str(DB), corpus_root=str(CORPUS),
               provider=ScriptedProvider([well_behaved_reply(spans)]),
               budget=Budget(interpretation_attempts=0))
    (ROOT / "demo_0_1_answer.html").write_text(render_html(good), encoding="utf-8")
    out["flagship"]["accepted"] = {
        "status": good.status, "answer": good.answer,
        "statements": [{"text": c["text"], "ok": c["ok"],
                        "evidence": [{"id": e["evidence_id"], "file": e["rel_path"],
                                      "bytes": [e["byte_start"], e["byte_end"]],
                                      "ok": e["ok"]} for e in c["evidence"]],
                        "structural": [{"assertion": f"{s['predicate']}({s['subject']}, {s['object']})",
                                        "status": s["status"]} for s in c["structural"]]}
                       for c in good.validation["claims"]],
        "evidence_retrieved": [e["evidence_id"] for e in good.evidence],
        "how_found": [e["how"] for e in good.evidence],
        "usage": good.usage, "attempts": good.attempts,
        "regenerations": good.regenerations, "latency_seconds": good.latency_seconds,
        "context_chars": sum(e["chars"] for e in good.evidence)}

    # 2. a model that invents a citation and an edge
    bad = ask(FLAGSHIP, db_path=str(DB), corpus_root=str(CORPUS),
              provider=ScriptedProvider([misbehaving_reply(), misbehaving_reply()]),
              budget=Budget(interpretation_attempts=0))
    out["flagship"]["rejected"] = {
        "status": bad.status, "abstain_reason": bad.abstain_reason,
        "validation_reason": bad.validation.get("reason"),
        "attempts": bad.attempts, "regenerations": bad.regenerations,
        "structural_status": bad.structural_status}

    # 3. the deterministic half on the ten target questions, no model at all
    for qid, q in TARGET:
        res = ask(q, db_path=str(DB), corpus_root=str(CORPUS), provider=None)
        out["targets"].append({
            "id": qid, "question": q,
            "evidence_retrieved": len(res.evidence),
            "context_chars": sum(e["chars"] for e in res.evidence),
            "how": sorted({e["how"].split(":")[0] for e in res.evidence}),
            "top_symbols": [e["symbol"] for e in res.evidence[:3]],
            "structural_facts": len(res.structural_facts),
            "latency_seconds": res.latency_seconds})

    (ROOT / "DEMO_0_1_RESULTS.json").write_text(json.dumps(out, indent=2))

    c = out["compile"]
    print(f"compiled: {c['counts']['artifact']} artifacts, {c['symbols']} symbols, "
          f"{c['claims']} claims, absent={c['absent']}, invariants={c['invariants']}")
    a = out["flagship"]["accepted"]
    print(f"\nflagship (contract-following model): {a['status']}  attempts={a['attempts']}  "
          f"context={a['context_chars']}B (~{a['context_chars']//4} tok)")
    for s in a["statements"]:
        print(f"   [{'ok' if s['ok'] else 'REJECTED'}] {s['text'][:78]}")
        for e in s["evidence"]:
            print(f"        evidence {e['id'][:12]} {e['file']} {e['bytes']} verified={e['ok']}")
        for st in s["structural"]:
            print(f"        {st['assertion']} -> {st['status']}")
    b = out["flagship"]["rejected"]
    print(f"\nflagship (misbehaving model): {b['status']}  attempts={b['attempts']}")
    print(f"   {b['validation_reason'][:140]}")
    print(f"\ndeterministic half on {len(out['targets'])} target questions (no model):")
    for t in out["targets"]:
        print(f"   {t['id']}  spans={t['evidence_retrieved']}  ctx={t['context_chars']:6d}B  "
              f"facts={t['structural_facts']:3d}  {t['top_symbols'][0][:44] if t['top_symbols'] else '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
