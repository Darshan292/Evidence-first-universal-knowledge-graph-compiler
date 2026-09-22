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
from kgc.ids import artifact_id as mk_artifact_id
from kgc.ids import config_hash, content_sha256, run_id as mk_run_id, source_id as mk_source_id
from kgc.ir import Artifact, Diagnostic, Modality, ParseStatus
from kgc.safety import classify, walk_corpus
from kgc.store import Store

STAGE = "extract"
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
    for path in walk_corpus(root, (".py",)):
        rel = str(path.relative_to(root))
        data, rej = classify(path, root=root)
        rep.seen += 1
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
            an = python_backend.analyze(aid, data, module_name=_module_name(rel))

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

            symbols, claims, refs = map_analysis(an, artifact_id=aid, data=data, run_id=run)
            for s in symbols:
                store.add_symbol(s)
            rep.symbols += len(symbols)

            if crash_at and crash_at[0] == "during_relationship" and n == crash_at[1]:
                raise CrashPoint("during_relationship")

            for claim, evidence in claims:
                store.add_claim(claim, evidence)
            rep.claims += len(claims)

            if crash_at and crash_at[0] == "during_evidence" and n == crash_at[1]:
                raise CrashPoint("during_evidence")

            for cid, to_name, res, reason in refs:
                store.add_reference_detail(cid, to_name, res, reason)
            for d in an.diagnostics:
                store.add_diagnostic(d, run)

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

    store.begin(); store.finish_run(run, "COMPLETE", _now()); store.commit()
    return rep


def _record_rejection(store: Store, src, rel, rej, run):
    aid = mk_artifact_id(src, rel, "rejected:" + rej.code)
    store.add_artifact(Artifact(
        artifact_id=aid, source_id=src, rel_path=rel, sha256="", size_bytes=0,
        media_type="application/octet-stream", modality=Modality.CODE,
        parse_status=ParseStatus.SKIPPED, parse_error=f"{rej.code}: {rej.message}"), run)
    store.add_diagnostic(Diagnostic(aid, "WARNING", rej.code, rej.message), run)


def _module_name(rel: str) -> str:
    p = rel[:-3] if rel.endswith(".py") else rel
    parts = [x for x in p.replace("\\", "/").split("/") if x]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or "__root__"
