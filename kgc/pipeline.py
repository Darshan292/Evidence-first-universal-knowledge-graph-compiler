"""Deterministic ingestion pipeline.

Transaction boundary (designed first, per Gate 1 §7): **one artifact = one
transaction**. Within it, evidence is written and verified before the claims
that cite it. A crash at any point leaves either a complete artifact or nothing
of it -- never a claim without evidence, never evidence without a claim.

Resume is idempotent because every identifier is content-addressed: re-running a
work item reproduces byte-identical rows, so `INSERT OR IGNORE` converges.

Two failure modes, deliberately distinguished (K-1.1 §8):

  CONTROLLED EXTRACTION FAILURE -- an exception while building the graph. The
    graph writes are discarded to a SAVEPOINT, but the ARTIFACT ROW SURVIVES,
    marked FAILED with a reason and a diagnostic. The artifact is the durable
    record of the failure. Before this, a plain rollback deleted the artifact
    too, so a parseable file could vanish leaving only a work_item error --
    seven of them did, on werkzeug.

  PROCESS CRASH -- the machine or process dies. Nothing can be written, so the
    whole transaction is lost and the work item stays RUNNING for resume to
    requeue. `CrashPoint` simulates exactly this and must keep doing so: it is
    re-raised past the extraction handler, never converted into a FAILED
    artifact.
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
from kgc.artifact_identity import canonical_path
from kgc.ids import artifact_id as mk_artifact_id
from kgc.ids import config_hash, content_sha256, run_id as mk_run_id, source_id as mk_source_id
from kgc.ir import Artifact, Diagnostic, Modality, ParseStatus
from kgc.safety import ANALYSED_SUFFIXES, classify, detect_language, walk_corpus
from kgc.store import Store

STAGE = "extract"
STAGE_RESOLVE = "resolve"
MAX_ATTEMPTS = 3   # a work item that crashes the process repeatedly is quarantined


class CrashPoint(Exception):
    """Test-only: simulates a PROCESS CRASH. The whole transaction is lost."""


class ExtractionFailure(Exception):
    """Test-only: simulates an internal extraction fault after the artifact row
    exists. The graph is discarded; the FAILED artifact survives."""


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
    extraction_failed: int = 0
    rejections: list[tuple[str, str]] = field(default_factory=list)
    # Walked, analysable files with no artifact row. Must always be empty: the
    # database cannot check this itself because it never sees the filesystem.
    absent: list[str] = field(default_factory=list)

    def as_dict(self):
        d = dict(self.__dict__)
        d["rejections"] = [{"path": p, "code": c} for p, c in self.rejections]
        return d


def _now():
    return datetime.now(timezone.utc).isoformat()


def ingest(store: Store, root: Path, *, resume: bool = True,
           crash_at: tuple[str, int] | None = None,
           fail_extraction_at: str | None = None,
           fail_resolution_at: str | None = None) -> IngestReport:
    """Ingest a corpus.

    `crash_at=(phase, nth_artifact)` simulates a PROCESS CRASH;
    `fail_extraction_at=<rel path>` and `fail_resolution_at=<rel path>` simulate
    a CONTROLLED EXTRACTION FAILURE in stage 1 and stage 2. All three are
    test-only and deliberately different: see the module docstring. None uses
    randomness or filesystem corruption.
    """
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
        rel = canonical_path(path.relative_to(root))
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

            store.savepoint("extraction")
            try:
                symbols, _, _, map_diags = map_analysis(
                    an, artifact_id=aid, data=data, run_id=run)
                for s in symbols:                  # ordered parent-before-child
                    store.add_symbol(s)

                if crash_at and crash_at[0] == "during_relationship" and n == crash_at[1]:
                    raise CrashPoint("during_relationship")
                if crash_at and crash_at[0] == "during_evidence" and n == crash_at[1]:
                    raise CrashPoint("during_evidence")
                if fail_extraction_at and fail_extraction_at == rel:
                    raise ExtractionFailure("injected extraction failure")

                for d in (*an.diagnostics, *map_diags):
                    store.add_diagnostic(d, run)
                store.enqueue(_resolve_item(item), run, STAGE_RESOLVE, rel, sha)
                # Extraction succeeded, so the artifact carries the analyser's
                # real status -- clearing any EXTRACTION_FAILED left by a
                # previous run. A later resolution failure may set it back.
                store.update_artifact_status(aid, an.parse_status.value, an.parse_error)
                store.release("extraction")
            except CrashPoint:
                raise                              # a crash is not a recorded failure
            except Exception as exc:
                store.rollback_to("extraction")    # graph discarded, artifact kept
                store.release("extraction")
                # The rollback only undoes THIS run. A previous run may have
                # built a complete graph for this artifact, and a FAILED
                # artifact must never carry one -- whoever built it.
                _purge_and_record(store, aid, rel, exc, run)
                store.finish_work(item, "FAILED", f"extraction failed: {exc}")
                store.commit()
                rep.failed += 1
                rep.extraction_failed += 1
                continue

            rep.symbols += len(symbols)
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

    _resolve_stage(store, root, src, run, rep, todo, fail_resolution_at)
    rep.absent = _verify_artifact_coverage(store, root)
    store.begin(); store.finish_run(run, "COMPLETE", _now()); store.commit()
    return rep


def _resolve_item(extract_item: str) -> str:
    return hashlib.sha256(f"{STAGE_RESOLVE}\x1f{extract_item}".encode()).hexdigest()


def _resolve_stage(store: Store, root: Path, src: str, run: str,
                   rep: "IngestReport", todo: list,
                   fail_resolution_at: str | None = None) -> None:
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
    # A resolve item exists only where stage 1 COMMITTED its symbols. Without
    # this guard, an artifact whose extraction failed still had claims written
    # here, against symbol ids that were rolled back -- a graph pointing at rows
    # that do not exist.
    enqueued = {r["item_id"] for r in store.con.execute(
        "SELECT item_id FROM work_item WHERE run_id=? AND stage=?",
        (run, STAGE_RESOLVE))}

    for item, rel, _path, data in todo:
        ritem = _resolve_item(item)
        if ritem in done or ritem not in enqueued:
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
            store.savepoint("resolution")
            try:
                # REPLACE this artifact's claims, never append to them: an edge
                # that resolved differently in an earlier run must not survive
                # beside its replacement.
                store.purge_artifact_claims(aid)
                _sym, claims, refs, map_diags = map_analysis(
                    an, artifact_id=aid, data=data, run_id=run,
                    resolved_refs=resolved,
                    extractor_version=f"{an.backend_version}+r{RESOLVER_VERSION}",
                    global_symbols=index.symbol_ids)
                for claim, evidence in claims:
                    store.add_claim(claim, evidence)
                for cid, to_name, res, reason in refs:
                    store.add_reference_detail(cid, to_name, res, reason)
                for d in map_diags:
                    store.add_diagnostic(d, run)
                if fail_resolution_at and fail_resolution_at == rel:
                    raise ExtractionFailure("injected resolution failure")
                # Resolution succeeded too: the artifact is whatever the parser
                # said it was, never a stale failure from an earlier run.
                store.update_artifact_status(aid, an.parse_status.value, an.parse_error)
                store.release("resolution")
            except Exception as exc:
                store.rollback_to("resolution")
                store.release("resolution")
                # Symbols were committed by stage 1. A FAILED artifact must not
                # keep half a graph, so they go too -- the artifact row stays.
                _purge_and_record(store, aid, rel, exc, run)
                store.finish_work(ritem, "FAILED", f"resolution failed: {exc}")
                store.commit()
                # The corpus symbol table just lost this artifact's symbols. A
                # later artifact resolving against the stale index would point a
                # fresh claim straight at a deleted row.
                index = ModuleIndex.from_store(store)
                # Stage 1 counted it as parsed; it is not. Keep the tally honest
                # so seen == parsed + failed + skipped + unsupported + unchanged.
                rep.parsed -= 1
                rep.failed += 1
                rep.extraction_failed += 1
                continue
            rep.claims += len(claims)
            store.finish_work(ritem, "DONE")
            store.commit()
        except Exception as e:
            store.rollback()
            store.begin(); store.finish_work(ritem, "FAILED", str(e)); store.commit()


def _record_extraction_failure(store: Store, aid: str, rel: str, exc: Exception, run: str):
    """The artifact is the durable record. Never let a parseable file vanish."""
    reason = f"{type(exc).__name__}: {exc}"
    store.mark_artifact_failed(aid, f"EXTRACTION_FAILED: {reason}")
    store.add_diagnostic(Diagnostic(
        aid, "ERROR", "EXTRACTION_FAILED",
        f"{rel}: graph extraction failed and was rolled back ({reason})"), run)


def _purge_and_record(store: Store, aid: str, rel: str, exc: Exception, run: str):
    """Discard this artifact's graph, then record the failure on the artifact.

    Purging can also drop edges other artifacts had into this one. That loss is
    recorded against each of THOSE artifacts -- it is their graph that changed.
    """
    for other_aid, other_rel, dropped in store.purge_artifact_graph(aid):
        store.add_diagnostic(Diagnostic(
            other_aid, "WARNING", "INBOUND_EDGES_DROPPED",
            f"{other_rel}: {dropped} edge(s) into {rel} were removed because that "
            f"artifact's extraction failed; they return when it succeeds"), run)
    _record_extraction_failure(store, aid, rel, exc, run)


def _verify_artifact_coverage(store: Store, root: Path) -> list[str]:
    """Ingestion postcondition: every walked analysable file has an artifact row.

    This cannot live in the database -- the database never sees the filesystem
    walk, so it cannot know a file is missing. It is checked here, where both
    halves are in hand, and returned on the report.
    """
    known = {r["rel_path"] for r in store.con.execute("SELECT rel_path FROM artifact")}
    return sorted(
        canonical_path(p.relative_to(root)) for p in walk_corpus(root)
        if p.suffix in ANALYSED_SUFFIXES
        and canonical_path(p.relative_to(root)) not in known)


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
    parts = [x for x in canonical_path(p).split("/") if x]   # one normalizer
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
