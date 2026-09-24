"""The Knowledge Graph Workbench: a small local web application.

Local-first. One process, one SQLite file per uploaded source, no cloud, no
account, no key needed to compile a repository, explore its graph or read its
evidence. A model provider is optional and only the semantic answer needs it.

The web layer is a thin shell. It calls `kgc.pipeline.ingest`, `kgq.graph` and
`kgq.answer.ask` -- there is no second answering path here, because J-2 will
measure the one in `kgq/answer.py` and a duplicate would make that measurement
meaningless.

    python3 -m kgq.web --port 8000
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from kgq import DEMO_VERSION
from kgq.answer import ABSTAIN_AMBIGUOUS, ANSWER, Budget, ask
from kgq.graph import GraphReader, describe_location
from kgq.provider import Provider, ProviderError
from kgq.workspace import (UnsafeArchive, WorkspaceStore, extract_zip, report_dicts,
                           write_single_file)

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "web_static"
WORKSPACES = Path(ROOT / "workspaces")
DEMO_CORPUS = ROOT / "eval/corpus3"

app = FastAPI(title="Evidence-first Knowledge Graph", version=DEMO_VERSION)
store = WorkspaceStore(WORKSPACES)

# compile runs off the request thread so the browser can show progress
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

STAGES = ["Uploading", "Extracting", "Compiling", "Building evidence", "Ready"]


def set_stage(wid: str, stage: str, **extra) -> None:
    with JOBS_LOCK:
        job = JOBS.setdefault(wid, {})
        job["stage"] = stage
        job["updated"] = time.time()
        job.update(extra)


def _compile(wid: str) -> None:
    from kgc.pipeline import ingest
    from kgc.store import Store
    ws = store.get(wid)
    try:
        set_stage(wid, "Compiling")
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(ws.db) + suffix)
            if p.exists():
                p.unlink()
        s = Store(str(ws.db))
        rep = ingest(s, ws.source.resolve())
        set_stage(wid, "Building evidence")
        violations = s.check_invariants()
        g = GraphReader(ws.db)
        stats = g.statistics()
        problems = g.problem_artifacts()
        g.close()
        s.close()
        ws.write_metadata(status="READY", counts=stats,
                          compile={"seen": rep.seen, "parsed": rep.parsed,
                                   "failed": rep.failed, "unsupported": rep.unsupported,
                                   "skipped": rep.skipped, "absent": rep.absent,
                                   "extraction_failed": rep.extraction_failed,
                                   "invariant_violations": violations},
                          problems=problems)
        set_stage(wid, "Ready", counts=stats)
    except Exception as e:                       # a failed compile is reported, not hidden
        ws.write_metadata(status="FAILED", error=f"{type(e).__name__}: {e}")
        set_stage(wid, "FAILED", error=f"{type(e).__name__}: {e}",
                  detail=traceback.format_exc(limit=3))


def start_compile(wid: str) -> None:
    threading.Thread(target=_compile, args=(wid,), daemon=True).start()


def need(wid: str):
    ws = store.get(wid)
    if ws is None:
        raise HTTPException(404, "no such workspace")
    return ws


def reader(ws):
    if not ws.db.is_file():
        raise HTTPException(409, "this workspace has not finished compiling")
    return GraphReader(ws.db)


# ── workspaces and ingestion ────────────────────────────────────────────
@app.get("/api/workspaces")
def list_workspaces():
    return {"workspaces": store.list(),
            "provider": provider_status()}


@app.post("/api/workspaces/upload")
async def upload(file: UploadFile = File(...), name: str = Form("")):
    body = await file.read()
    ws = store.create(name or file.filename or "upload")
    set_stage(ws.id, "Uploading")
    try:
        set_stage(ws.id, "Extracting")
        if (file.filename or "").lower().endswith(".zip"):
            reports = extract_zip(body, ws.source)
        else:
            reports = [write_single_file(file.filename or "upload", body, ws.source)]
    except UnsafeArchive as e:
        ws.write_metadata(status="REJECTED", error=str(e))
        set_stage(ws.id, "FAILED", error=str(e))
        return JSONResponse({"id": ws.id, "status": "REJECTED", "error": str(e)},
                            status_code=400)
    ws.write_metadata(status="EXTRACTED", files=report_dicts(reports),
                      source_name=file.filename)
    start_compile(ws.id)
    return {"id": ws.id, "status": "EXTRACTED", "files": report_dicts(reports)}


@app.post("/api/workspaces/demo")
def demo():
    """Compile the bundled Werkzeug corpus into a fresh workspace."""
    if not DEMO_CORPUS.is_dir():
        raise HTTPException(404, "the demo corpus is not present in this checkout")
    ws = store.create("Werkzeug (demo corpus)")
    # the corpus is referenced, not copied into frontend assets
    ws.source.rmdir()
    ws.source.symlink_to(DEMO_CORPUS.resolve(), target_is_directory=True)
    ws.write_metadata(status="EXTRACTED", files=[], source_name="eval/corpus3")
    start_compile(ws.id)
    return {"id": ws.id, "status": "EXTRACTED"}


@app.get("/api/workspaces/{wid}/status")
def status(wid: str):
    ws = need(wid)
    with JOBS_LOCK:
        job = dict(JOBS.get(wid, {}))
    meta = ws.metadata()
    return {"id": wid, "stage": job.get("stage", meta.get("status", "CREATED")),
            "stages": STAGES, "error": job.get("error") or meta.get("error"),
            "metadata": meta}


@app.delete("/api/workspaces/{wid}")
def remove(wid: str):
    return {"deleted": store.delete(wid)}


# ── graph ───────────────────────────────────────────────────────────────
@app.get("/api/workspaces/{wid}/stats")
def stats(wid: str):
    g = reader(need(wid))
    try:
        return {"statistics": g.statistics(), "problems": g.problem_artifacts()}
    finally:
        g.close()


@app.get("/api/workspaces/{wid}/search")
def search(wid: str, q: str = ""):
    g = reader(need(wid))
    try:
        return {"results": g.search_symbols(q)}
    finally:
        g.close()


@app.get("/api/workspaces/{wid}/node/{symbol_id}")
def node(wid: str, symbol_id: str):
    g = reader(need(wid))
    try:
        n = g.node(symbol_id)
        if n is None:
            raise HTTPException(404, "no such symbol")
        return n
    finally:
        g.close()


@app.get("/api/workspaces/{wid}/neighbourhood/{symbol_id}")
def neighbourhood(wid: str, symbol_id: str, hops: int = 1, predicates: str = ""):
    g = reader(need(wid))
    try:
        preds = tuple(p for p in predicates.split(",") if p) or None
        return g.neighbourhood(symbol_id, hops=hops,
                               **({"predicates": preds} if preds else {}))
    finally:
        g.close()


@app.get("/api/workspaces/{wid}/edge/{claim_id}")
def edge(wid: str, claim_id: str):
    g = reader(need(wid))
    try:
        e = g.edge(claim_id)
        if e is None:
            raise HTTPException(404, "no such claim")
        return e
    finally:
        g.close()


@app.get("/api/workspaces/{wid}/symbol_source/{symbol_id}")
def symbol_source(wid: str, symbol_id: str):
    """The source region a symbol occupies, re-read from disk.

    The symbol's own locator is the address; nothing here re-derives one.
    """
    ws = need(wid)
    g = reader(ws)
    try:
        n = g.node(symbol_id)
        if n is None:
            raise HTTPException(404, "no such symbol")
    finally:
        g.close()
    quoted = ""
    if n["locator_kind"] != "byte_range":
        # a document unit has no line identity; re-extract its text rather than
        # inventing an address the format cannot carry
        from kgc.analysis.mapper import quote_for
        from kgc.ir import Locator, LocatorKind
        path = ws.source / n["rel_path"]
        if path.is_file():
            try:
                quoted = quote_for(path.read_bytes(),
                                   Locator(LocatorKind(n["locator_kind"]), n["locator"]))
            except Exception:
                quoted = ""
    shape = {"rel_path": n["rel_path"], "locator_kind": n["locator_kind"],
             "locator": n["locator"], "quoted_text": quoted}
    return {"symbol_id": symbol_id, "rel_path": n["rel_path"],
            "location": describe_location(n["locator_kind"], n["locator"]),
            "context": source_context(ws, shape)}


@app.get("/api/workspaces/{wid}/evidence/{evidence_id}")
def evidence(wid: str, evidence_id: str):
    """The evidence record plus the surrounding source, re-read from disk."""
    ws = need(wid)
    g = reader(ws)
    try:
        ev = g.evidence(evidence_id)
        if ev is None:
            raise HTTPException(404, "no such evidence")
    finally:
        g.close()
    ev["context"] = source_context(ws, ev)
    return ev


def source_context(ws, ev: dict, window: int = 6) -> dict:
    """Show the cited region inside its file, when the format has line identity.

    A PDF page or a DOCX paragraph has no line numbers a reader could trust, so
    none are manufactured: the extracted unit text is returned as-is.
    """
    path = ws.source / ev["rel_path"]
    if ev["locator_kind"] != "byte_range":
        return {"kind": ev["locator_kind"], "text": ev["quoted_text"],
                "note": "extracted text; this format carries no line numbers"}
    if not path.is_file():
        return {"kind": "missing", "text": "", "note": "the source file is no longer present"}
    data = path.read_bytes()
    loc = ev["locator"]
    start, end = loc.get("byte_start", 0), loc.get("byte_end", 0)
    lines = data.decode("utf-8", "replace").splitlines()
    first = max(0, data[:start].count(b"\n") - window)
    last = min(len(lines), data[:end].count(b"\n") + 1 + window)
    hl_from = data[:start].count(b"\n") + 1
    hl_to = data[:end].count(b"\n") + 1
    return {"kind": "byte_range", "first_line": first + 1,
            "highlight_from": hl_from, "highlight_to": hl_to,
            "lines": lines[first:last], "note": ""}


# ── questions: one path, the existing one ───────────────────────────────
def provider_status() -> dict:
    try:
        p = Provider.from_env(cache_path=str(WORKSPACES / "model_cache.sqlite"))
        return {"configured": True, "model": p.model, "base_url": p.base_url}
    except ProviderError as e:
        return {"configured": False, "reason": str(e)}


@app.get("/api/provider")
def provider():
    return provider_status()


@app.post("/api/workspaces/{wid}/ask")
def ask_question(wid: str, payload: dict):
    ws = need(wid)
    question = (payload or {}).get("question", "").strip()
    if not question:
        raise HTTPException(400, "a question is required")
    if not ws.db.is_file():
        raise HTTPException(409, "this workspace has not finished compiling")
    p = None
    try:
        p = Provider.from_env(cache_path=str(WORKSPACES / "model_cache.sqlite"))
    except ProviderError:
        pass                                   # deterministic half still answers
    result = ask(question, db_path=str(ws.db), corpus_root=str(ws.source.resolve()),
                 provider=p,
                 budget=Budget(max_context_chars=int((payload or {}).get("max_chars", 24000))))
    out = result.as_dict()
    out["is_answer"] = result.status == ANSWER
    out["is_ambiguous"] = result.status == ABSTAIN_AMBIGUOUS
    return out


# ── static shell ────────────────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def main() -> int:
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser(prog="kgq.web")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    WORKSPACES.mkdir(parents=True, exist_ok=True)
    print(f"Knowledge Graph Workbench -> http://{a.host}:{a.port}")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
