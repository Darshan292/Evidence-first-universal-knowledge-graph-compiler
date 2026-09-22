"""Deterministic ingestion pipeline.

Transaction boundary (designed first, per Gate 1 §7): **one artifact = one
transaction**. Within it, evidence is written and verified before the claims
that cite it. A crash at any point leaves either a complete artifact or nothing
of it -- never a claim without evidence, never evidence without a claim.

Resume is idempotent because every identifier is content-addressed: re-running a
work item reproduces byte-identical rows, so `INSERT OR IGNORE` converges.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from kgc import SOFTWARE_VERSION
from kgc.analysis import python_backend
from kgc.analysis.mapper import map_analysis
from kgc.analysis.resolver import (RESOLVER_VERSION, ModuleIndex,
                                   collect_reexports, resolve)
from kgc.ids import artifact_id as mk_artifact_id
from kgc.ids import config_hash, content_sha256, run_id as mk_run_id, source_id as mk_source_id
from kgc.ir import Artifact, Diagnostic, Modality, ParseStatus
from kgc.safety import ANALYSED_SUFFIXES, classify, detect_language, walk_corpus
from kgc.store import Store

STAGE = "extract"
STAGE_RESOLVE = "resolve"
MAX_ATTEMPTS = 3   # a work item that crashes the process repeatedly is quarantined


class CrashPoint(Exception):
    """Test-only: injected to prove the transaction boundary holds."""


@dataclass
class IngestReport:
    run_id: str
    seen: int = 0
    parsed: int = 0
    failed: int = 0
    skipped: int = 0
    unsupported: int = 0
    unchanged: int = 0
    symbols: int = 0
    claims: int = 0
    rejections: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self):
        d = dict(self.__dict__)
        d["rejections"] = [{"path": p, "code": c} for p, c in self.rejections]
        return d


def _now():
    return datetime.now(timezone.utc).isoformat()


def ingest(store: Store, root: Path, *, resume: bool = True,
           crash_at: tuple[str, int] | None = None) -> IngestReport:
    """Ingest a corpus. `crash_at=(phase, nth_artifact)` is test-only."""
    root = Path(root).resolve(strict=True)
    cfg = {"root": str(root), "backend": python_backend.BACKEND_ID,
           "backend_version": python_backend.BACKEND_VERSION,
           "resolver_version": RESOLVER_VERSION,
           "software_version": SOFTWARE_VERSION, "suffixes": [".py"]}
    cfg_h = config_hash(cfg)

    prior = store.find_resumable_run() if resume else None
    if prior and prior["config_hash"] == cfg_h:
        run = prior["run_id"]
        store.begin(); store.requeue_running(run); store.commit()
    else:
        run = mk_run_id(SOFTWARE_VERSION, cfg_h, _now())
        store.begin()
        store.add_run(run_id=run, started_at=_now(), software_version=SOFTWARE_VERSION,
                      config_hash=cfg_h, config_json=__import__("json").dumps(cfg, sort_keys=True))
        store.commit()

    rep = IngestReport(run_id=run)
    src = mk_source_id(str(root))
    store.begin(); store.add_source(src, str(root), "directory"); store.commit()

    # ── enqueue (its own transaction, so the worklist survives a crash) ──
    store.begin()
    todo = []
    for path in walk_corpus(root):
        rel = str(path.relative_to(root))
        rep.seen += 1
        if path.suffix not in ANALYSED_SUFFIXES:
            lang = detect_language(path) or "unknown"
            rep.unsupported += 1
            rep.rejections.append((rel, f"UNSUPPORTED_LANGUAGE:{lang}"))
            _record_unsupported(store, src, rel, path, lang, run)
            continue
        data, rej = classify(path, root=root)
        if rej is not None:
            rep.skipped += 1
            rep.rejections.append((rel, rej.code))
            _record_rejection(store, src, rel, rej, run)
            continue
        item = hashlib.sha256(f"{STAGE}\x1f{rel}\x1f{content_sha256(data)}".encode()).hexdigest()
        store.enqueue(item, run, STAGE, rel, content_sha256(data))
        todo.append((item, rel, path, data))
    store.commit()

    done = {r["item_id"] for r in store.con.execute(
        "SELECT item_id FROM work_item WHERE run_id=? AND state='DONE'", (run,))}

    # ── per-artifact transaction ────────────────────────────────────────
    for n, (item, rel, path, data) in enumerate(todo, 1):
        if item in done:
            rep.unchanged += 1
            continue

        # Claim the item in its OWN committed transaction. If this were inside
        # the work transaction, a crash would roll back both the RUNNING state
        # and the attempts counter -- so a file that crashes the process would
        # be retried forever. Found by test_interrupted_run_is_resumable.
        store.begin()
        attempts = store.con.execute(
            "SELECT attempts FROM work_item WHERE item_id=?", (item,)).fetchone()["attempts"]
        if attempts >= MAX_ATTEMPTS:
            store.finish_work(item, "FAILED", f"quarantined after {attempts} attempts")
            store.commit()
            rep.failed += 1
            continue
        store.claim_work(item)
        store.commit()

        store.begin()
        try:
            if crash_at and crash_at[0] == "before_insert" and n == crash_at[1]:
                raise CrashPoint("before_insert")

            sha = content_sha256(data)
            aid = mk_artifact_id(src, rel, sha)
            an = python_backend.analyze(aid, data, module_name=_module_name(rel, root))

            store.add_artifact(Artifact(
                artifact_id=aid, source_id=src, rel_path=rel, sha256=sha,
                size_bytes=len(data), media_type="text/x-python", modality=Modality.CODE,
                parse_status=an.parse_status, parse_error=an.parse_error,
                parser_id=an.backend_id, parser_version=an.backend_version), run)

            if crash_at and crash_at[0] == "during_entity" and n == crash_at[1]:
                raise CrashPoint("during_entity")

            if an.parse_status is ParseStatus.FAILED:
                rep.failed += 1
                for d in an.diagnostics:
                    store.add_diagnostic(d, run)
                store.finish_work(item, "DONE")
                store.commit()
                continue

            symbols, _, _ = map_analysis(an, artifact_id=aid, data=data, run_id=run)
            for s in symbols:
                store.add_symbol(s)
            rep.symbols += len(symbols)

            if crash_at and crash_at[0] == "during_relationship" and n == crash_at[1]:
                raise CrashPoint("during_relationship")
            if crash_at and crash_at[0] == "during_evidence" and n == crash_at[1]:
                raise CrashPoint("during_evidence")

            for d in an.diagnostics:
                store.add_diagnostic(d, run)
            store.enqueue(_resolve_item(item), run, STAGE_RESOLVE, rel, sha)
            store.finish_work(item, "DONE")
            if crash_at and crash_at[0] == "before_commit" and n == crash_at[1]:
                raise CrashPoint("before_commit")
            store.commit()
            rep.parsed += 1
            if crash_at and crash_at[0] == "after_commit" and n == crash_at[1]:
                raise CrashPoint("after_commit")
        except CrashPoint:
            store.rollback()
            raise
        except Exception as e:
            store.rollback()
            store.begin(); store.finish_work(item, "FAILED", str(e)); store.commit()
            rep.failed += 1

    _resolve_stage(store, root, src, run, rep, todo)
    store.begin(); store.finish_run(run, "COMPLETE", _now()); store.commit()
    return rep


def _resolve_item(extract_item: str) -> str:
    return hashlib.sha256(f"{STAGE_RESOLVE}\x1f{extract_item}".encode()).hexdigest()


def _resolve_stage(store: Store, root: Path, src: str, run: str,
                   rep: "IngestReport", todo: list) -> None:
    """Stage 2: resolve references against the corpus-wide symbol table.

    The symbol table is read from the database rather than held in memory, so
    this stage costs no extra memory and resumes independently of stage 1.
    """
    index = ModuleIndex.from_store(store)

    analyses: dict[str, object] = {}
    for _item, rel, _path, data in todo:
        mod = _module_name(rel, root)
        sha = content_sha256(data)
        aid = mk_artifact_id(src, rel, sha)
        an = python_backend.analyze(aid, data, module_name=mod)
        if an.parse_status is not ParseStatus.FAILED:
            analyses[mod] = an
    reexports = collect_reexports(analyses)

    done = {r["item_id"] for r in store.con.execute(
        "SELECT item_id FROM work_item WHERE run_id=? AND stage=? AND state='DONE'",
        (run, STAGE_RESOLVE))}

    for item, rel, _path, data in todo:
        ritem = _resolve_item(item)
        if ritem in done:
            continue
        mod = _module_name(rel, root)
        an = analyses.get(mod)
        if an is None:
            store.begin(); store.finish_work(ritem, "SKIPPED"); store.commit()
            continue
        sha = content_sha256(data)
        aid = mk_artifact_id(src, rel, sha)
        resolved = resolve(an, mod, index, reexports)

        store.begin()
        try:
            store.claim_work(ritem)
            _sym, claims, refs = map_analysis(
                an, artifact_id=aid, data=data, run_id=run,
                resolved_refs=resolved,
                extractor_version=f"{an.backend_version}+r{RESOLVER_VERSION}",
                global_symbols=index.symbol_ids)
            for claim, evidence in claims:
                store.add_claim(claim, evidence)
            for cid, to_name, res, reason in refs:
                store.add_reference_detail(cid, to_name, res, reason)
            rep.claims += len(claims)
            store.finish_work(ritem, "DONE")
            store.commit()
        except Exception as e:
            store.rollback()
            store.begin(); store.finish_work(ritem, "FAILED", str(e)); store.commit()


def _record_unsupported(store: Store, src, rel, path, lang, run):
    """An unanalysed file is a recorded fact, never an absence."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    aid = mk_artifact_id(src, rel, f"unsupported:{lang}")
    store.add_artifact(Artifact(
        artifact_id=aid, source_id=src, rel_path=rel, sha256="", size_bytes=size,
        media_type="application/octet-stream", modality=Modality.CODE,
        parse_status=ParseStatus.UNSUPPORTED,
        parse_error=f"no analyser for language {lang!r}"), run)
    store.add_diagnostic(Diagnostic(aid, "INFO", "UNSUPPORTED_LANGUAGE",
                                    f"{rel}: language {lang!r} has no analyser"), run)


def _record_rejection(store: Store, src, rel, rej, run):
    aid = mk_artifact_id(src, rel, "rejected:" + rej.code)
    store.add_artifact(Artifact(
        artifact_id=aid, source_id=src, rel_path=rel, sha256="", size_bytes=0,
        media_type="application/octet-stream", modality=Modality.CODE,
        parse_status=ParseStatus.SKIPPED, parse_error=f"{rej.code}: {rej.message}"), run)
    store.add_diagnostic(Diagnostic(aid, "WARNING", rej.code, rej.message), run)


def _module_name(rel: str, root: Path | None = None) -> str:
    """Name a module by its PACKAGE root, not by the ingestion root.

    `src/itsdangerous/signer.py` must be `itsdangerous.signer`, because that is
    what its own imports say. Naming it `src.itsdangerous.signer` made every
    absolute import miss, silently losing every cross-file edge -- measured as
    0 edges on a real repository where the correct naming yields many.
    """
    p = rel[:-3] if rel.endswith(".py") else rel
    parts = [x for x in p.replace("\\", "/").split("/") if x]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if root is not None and len(parts) > 1:
        # drop leading directories that are not Python packages
        for i in range(len(parts) - 1):
            pkg_dir = root.joinpath(*parts[: i + 1])
            if (pkg_dir / "__init__.py").exists():
                parts = parts[i:]
                break
        else:
            parts = parts[-1:] if (root / parts[0]).is_dir() and not (
                root / parts[0] / "__init__.py").exists() else parts
    return ".".join(parts) or "__root__"
