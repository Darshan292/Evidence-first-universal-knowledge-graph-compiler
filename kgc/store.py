"""SQLite persistence. The ONLY module in the system containing SQL.

This is a boundary, not a framework: no base classes, no registry, no second
implementation written speculatively. If the engine ever changes, one file is
rewritten.

Invariants are enforced in the database itself (triggers), not only in Python,
so they hold even against a future caller that bypasses this API.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from kgc import SCHEMA_VERSION
from kgc.ids import canonical_json, diagnostic_id
from kgc.ir import Artifact, Claim, Diagnostic, Evidence, Symbol

STRUCTURAL_SQL_LIST = "'CONTAINS','DEFINES','IMPORTS','CALLS','REFERENCES','EXTENDS','IMPLEMENTS','READS','WRITES'"

SCHEMA = f"""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS processing_run(
  run_id           TEXT PRIMARY KEY,
  started_at       TEXT NOT NULL,
  finished_at      TEXT,
  software_version TEXT NOT NULL,
  schema_version   TEXT NOT NULL,
  config_hash      TEXT NOT NULL,
  config_json      TEXT NOT NULL,
  model_provider   TEXT,
  model_id         TEXT,
  prompt_version   TEXT,
  status           TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETE','FAILED','INTERRUPTED'))
);

CREATE TABLE IF NOT EXISTS source(
  source_id TEXT PRIMARY KEY,
  uri       TEXT NOT NULL,
  kind      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact(
  artifact_id   TEXT PRIMARY KEY,
  source_id     TEXT NOT NULL REFERENCES source(source_id),
  rel_path      TEXT NOT NULL,
  sha256        TEXT NOT NULL,
  size_bytes    INTEGER NOT NULL,
  media_type    TEXT NOT NULL,
  modality      TEXT NOT NULL,
  parse_status  TEXT NOT NULL CHECK(parse_status IN ('OK','PARTIAL','FAILED','SKIPPED')),
  parse_error   TEXT,
  parser_id     TEXT,
  parser_version TEXT,
  first_seen_run TEXT NOT NULL REFERENCES processing_run(run_id)
);
CREATE INDEX IF NOT EXISTS i_artifact_path ON artifact(rel_path);
CREATE INDEX IF NOT EXISTS i_artifact_sha  ON artifact(sha256);

CREATE TABLE IF NOT EXISTS symbol(
  symbol_id      TEXT PRIMARY KEY,
  artifact_id    TEXT NOT NULL REFERENCES artifact(artifact_id),
  parent_id      TEXT REFERENCES symbol(symbol_id),
  kind           TEXT NOT NULL,
  name           TEXT NOT NULL,
  qualified_name TEXT NOT NULL,
  locator_kind   TEXT NOT NULL,
  locator        TEXT NOT NULL,
  docstring      TEXT
);
CREATE INDEX IF NOT EXISTS i_symbol_qn  ON symbol(qualified_name);
CREATE INDEX IF NOT EXISTS i_symbol_art ON symbol(artifact_id);

-- Resolution DETAIL for reference-style claims. Deliberately NOT a parallel
-- fact store: the claim holds subject/predicate/object/evidence, this holds only
-- how well the target was resolved. Collapsed during implementation after the
-- two tables were found to duplicate each other.
CREATE TABLE IF NOT EXISTS reference(
  claim_id   TEXT PRIMARY KEY REFERENCES claim(claim_id),
  to_name    TEXT NOT NULL,
  resolution TEXT NOT NULL CHECK(resolution IN ('DETERMINISTIC','HEURISTIC','UNRESOLVED')),
  reason     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS i_ref_res ON reference(resolution);

CREATE TABLE IF NOT EXISTS evidence(
  evidence_id  TEXT PRIMARY KEY,
  artifact_id  TEXT NOT NULL REFERENCES artifact(artifact_id),
  artifact_sha256 TEXT NOT NULL,
  locator_kind TEXT NOT NULL,
  locator      TEXT NOT NULL,
  quoted_text  TEXT NOT NULL,
  verification_strength TEXT CHECK(verification_strength IN ('EXACT','REPRODUCIBLE','STRUCTURAL')),
  verifier_engine TEXT,
  verified_at  TEXT
);

CREATE TABLE IF NOT EXISTS claim(
  claim_id      TEXT PRIMARY KEY,
  predicate     TEXT NOT NULL,
  subject_id    TEXT NOT NULL,
  subject_kind  TEXT NOT NULL,
  object_id     TEXT,
  object_kind   TEXT,
  object_literal TEXT,
  lifecycle     TEXT NOT NULL CHECK(lifecycle IN
                  ('CANDIDATE','VALIDATING','VERIFIED','ACTIVE','REJECTED','SUPERSEDED')),
  establishment TEXT NOT NULL CHECK(establishment IN
                  ('DERIVED','CONFIRMED','PROPOSED','DISPUTED')),
  confidence    REAL,
  extractor_id  TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  model_id      TEXT,
  prompt_version TEXT,
  schema_version TEXT NOT NULL,
  run_id        TEXT NOT NULL REFERENCES processing_run(run_id),
  evidence_ids  TEXT NOT NULL,
  rejected_reason TEXT
);
CREATE INDEX IF NOT EXISTS i_claim_subject ON claim(subject_id, predicate);
CREATE INDEX IF NOT EXISTS i_claim_est     ON claim(establishment);

CREATE TABLE IF NOT EXISTS claim_evidence(
  claim_id    TEXT NOT NULL REFERENCES claim(claim_id),
  evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
  PRIMARY KEY(claim_id, evidence_id)
);
-- Without this the dangling-evidence audit scans claim_evidence once per
-- evidence row: measured 2.214s -> 0.003s at 600 files, full audit 7.5s -> 0.04s.
CREATE INDEX IF NOT EXISTS i_ce_evidence ON claim_evidence(evidence_id);

CREATE TABLE IF NOT EXISTS claim_relation(
  from_claim TEXT NOT NULL REFERENCES claim(claim_id),
  to_claim   TEXT NOT NULL REFERENCES claim(claim_id),
  kind       TEXT NOT NULL CHECK(kind IN ('SUPPORTS','CONTRADICTS','SUPERSEDES','DERIVED_FROM')),
  reason     TEXT NOT NULL,
  run_id     TEXT NOT NULL REFERENCES processing_run(run_id),
  PRIMARY KEY(from_claim, to_claim, kind)
);

-- diagnostic_id is content-addressed so re-ingestion converges instead of
-- accumulating duplicates (bug found by the idempotency test).
CREATE TABLE IF NOT EXISTS diagnostic(
  diagnostic_id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL REFERENCES artifact(artifact_id),
  run_id      TEXT NOT NULL REFERENCES processing_run(run_id),
  severity    TEXT NOT NULL,
  code        TEXT NOT NULL,
  message     TEXT NOT NULL,
  line        INTEGER
);
CREATE INDEX IF NOT EXISTS i_diag_code ON diagnostic(code);

CREATE TABLE IF NOT EXISTS work_item(
  item_id    TEXT PRIMARY KEY,
  run_id     TEXT NOT NULL REFERENCES processing_run(run_id),
  stage      TEXT NOT NULL,
  target     TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  state      TEXT NOT NULL CHECK(state IN ('PENDING','RUNNING','DONE','FAILED','SKIPPED')),
  attempts   INTEGER NOT NULL DEFAULT 0,
  error      TEXT
);
CREATE INDEX IF NOT EXISTS i_work_state ON work_item(run_id, state);

-- ── Executable invariants ────────────────────────────────────────────────
-- Evidence is inserted and verified BEFORE the claim, in the same transaction,
-- so these triggers see a complete picture (ADR-0006 Part 4).

CREATE TRIGGER IF NOT EXISTS claim_requires_evidence
AFTER INSERT ON claim
WHEN (SELECT count(*) FROM json_each(NEW.evidence_ids)) = 0
BEGIN SELECT RAISE(ABORT, 'INVARIANT: claim has no evidence'); END;

CREATE TRIGGER IF NOT EXISTS claim_evidence_must_exist
AFTER INSERT ON claim
WHEN EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids) je
             WHERE NOT EXISTS (SELECT 1 FROM evidence e WHERE e.evidence_id = je.value))
BEGIN SELECT RAISE(ABORT, 'INVARIANT: claim references non-existent evidence'); END;

CREATE TRIGGER IF NOT EXISTS trusted_claim_needs_verified_evidence
AFTER INSERT ON claim
WHEN NEW.establishment IN ('DERIVED','CONFIRMED')
 AND NOT EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids) je
                 JOIN evidence e ON e.evidence_id = je.value
                 WHERE e.verification_strength IN ('EXACT','REPRODUCIBLE'))
BEGIN SELECT RAISE(ABORT, 'INVARIANT: trusted claim lacks EXACT/REPRODUCIBLE evidence'); END;

CREATE TRIGGER IF NOT EXISTS model_claim_needs_verified_evidence
AFTER INSERT ON claim
WHEN NEW.establishment IN ('PROPOSED','DISPUTED')
 AND NOT EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids) je
                 JOIN evidence e ON e.evidence_id = je.value
                 WHERE e.verification_strength IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'INVARIANT: model claim has unverified evidence'); END;

-- A model or heuristic may never establish a parser-level structural fact.
CREATE TRIGGER IF NOT EXISTS structural_predicate_requires_derivation
AFTER INSERT ON claim
WHEN NEW.predicate IN ({STRUCTURAL_SQL_LIST})
 AND NEW.establishment NOT IN ('DERIVED','CONFIRMED')
BEGIN SELECT RAISE(ABORT, 'INVARIANT: structural predicate requires DERIVED/CONFIRMED'); END;

-- STRUCTURAL-only evidence can never support a CONFIRMED claim.
CREATE TRIGGER IF NOT EXISTS structural_evidence_cannot_confirm
AFTER INSERT ON claim
WHEN NEW.establishment = 'CONFIRMED'
 AND NOT EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids) je
                 JOIN evidence e ON e.evidence_id = je.value
                 WHERE e.verification_strength IN ('EXACT','REPRODUCIBLE'))
BEGIN SELECT RAISE(ABORT, 'INVARIANT: CONFIRMED needs EXACT/REPRODUCIBLE evidence'); END;

-- A DERIVED claim may not carry model identity: it was not produced by a model.
CREATE TRIGGER IF NOT EXISTS derived_claim_has_no_model
AFTER INSERT ON claim
WHEN NEW.establishment = 'DERIVED' AND NEW.model_id IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'INVARIANT: DERIVED claim must not have model_id'); END;

CREATE TRIGGER IF NOT EXISTS rejected_claim_needs_reason
AFTER INSERT ON claim
WHEN NEW.lifecycle = 'REJECTED' AND (NEW.rejected_reason IS NULL OR NEW.rejected_reason = '')
BEGIN SELECT RAISE(ABORT, 'INVARIANT: REJECTED claim needs a reason'); END;
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.con = sqlite3.connect(self.path, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        cur = self.con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if cur is None:
            self.con.execute("INSERT INTO meta VALUES('schema_version',?)", (SCHEMA_VERSION,))
        elif cur["value"] != SCHEMA_VERSION:
            raise RuntimeError(f"schema {cur['value']} != expected {SCHEMA_VERSION}")

    # ── transaction boundary ────────────────────────────────────────────
    def begin(self):
        self.con.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.con.execute("COMMIT")

    def rollback(self):
        try:
            self.con.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass

    def close(self):
        self.con.close()

    # ── writes ──────────────────────────────────────────────────────────
    def add_run(self, *, run_id, started_at, software_version, config_hash, config_json,
                model_provider=None, model_id=None, prompt_version=None):
        self.con.execute(
            "INSERT OR IGNORE INTO processing_run(run_id,started_at,software_version,"
            "schema_version,config_hash,config_json,model_provider,model_id,prompt_version,status)"
            " VALUES(?,?,?,?,?,?,?,?,?, 'RUNNING')",
            (run_id, started_at, software_version, SCHEMA_VERSION, config_hash,
             config_json, model_provider, model_id, prompt_version))

    def finish_run(self, run_id, status, finished_at):
        self.con.execute("UPDATE processing_run SET status=?, finished_at=? WHERE run_id=?",
                         (status, finished_at, run_id))

    def add_source(self, source_id, uri, kind):
        self.con.execute("INSERT OR IGNORE INTO source VALUES(?,?,?)", (source_id, uri, kind))

    def add_artifact(self, a: Artifact, run_id: str):
        self.con.execute(
            "INSERT OR IGNORE INTO artifact(artifact_id,source_id,rel_path,sha256,size_bytes,"
            "media_type,modality,parse_status,parse_error,parser_id,parser_version,first_seen_run)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (a.artifact_id, a.source_id, a.rel_path, a.sha256, a.size_bytes, a.media_type,
             a.modality.value, a.parse_status.value, a.parse_error, a.parser_id,
             a.parser_version, run_id))

    def add_symbol(self, s: Symbol):
        self.con.execute(
            "INSERT OR IGNORE INTO symbol VALUES(?,?,?,?,?,?,?,?,?)",
            (s.symbol_id, s.artifact_id, s.parent_id, s.kind, s.name, s.qualified_name,
             s.locator.kind.value, canonical_json(s.locator.payload), s.docstring))

    def add_reference_detail(self, claim_id: str, to_name: str, resolution: str, reason: str):
        self.con.execute("INSERT OR IGNORE INTO reference VALUES(?,?,?,?)",
                         (claim_id, to_name, resolution, reason))

    def add_claim(self, claim: Claim, evidence: list[Evidence]):
        """Evidence first, then the claim, then the links -- one transaction.

        A crash at any point before commit leaves nothing, so orphan evidence
        and orphan claims are both unrepresentable after a committed write.
        """
        for e in evidence:
            self.con.execute(
                "INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?,?)",
                (e.evidence_id, e.artifact_id, e.artifact_sha256, e.locator.kind.value,
                 canonical_json(e.locator.payload), e.quoted_text,
                 e.verification_strength.value if e.verification_strength else None,
                 e.verifier_engine, e.verified_at))
        self.con.execute(
            "INSERT OR IGNORE INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (claim.claim_id, claim.predicate, claim.subject_id, claim.subject_kind,
             claim.object_id, claim.object_kind, claim.object_literal,
             claim.lifecycle.value, claim.establishment.value, claim.confidence,
             claim.extractor_id, claim.extractor_version, claim.model_id,
             claim.prompt_version, claim.schema_version, claim.run_id,
             canonical_json(sorted(claim.evidence_ids)), claim.rejected_reason))
        for eid in claim.evidence_ids:
            self.con.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?)",
                             (claim.claim_id, eid))

    def add_claim_relation(self, from_claim, to_claim, kind, reason, run_id):
        self.con.execute("INSERT OR IGNORE INTO claim_relation VALUES(?,?,?,?,?)",
                         (from_claim, to_claim, kind, reason, run_id))

    def add_diagnostic(self, d: Diagnostic, run_id: str):
        did = diagnostic_id(d.artifact_id, d.severity, d.code, d.message, d.line)
        self.con.execute("INSERT OR IGNORE INTO diagnostic VALUES(?,?,?,?,?,?,?)",
                         (did, d.artifact_id, run_id, d.severity, d.code, d.message, d.line))

    # ── work items (crash resume) ───────────────────────────────────────
    def enqueue(self, item_id, run_id, stage, target, input_hash):
        self.con.execute(
            "INSERT OR REPLACE INTO work_item(item_id,run_id,stage,target,input_hash,state,attempts)"
            " VALUES(?,?,?,?,?,'PENDING',COALESCE((SELECT attempts FROM work_item WHERE item_id=?),0))",
            (item_id, run_id, stage, target, input_hash, item_id))

    def claim_work(self, item_id):
        self.con.execute(
            "UPDATE work_item SET state='RUNNING', attempts=attempts+1 WHERE item_id=?", (item_id,))

    def finish_work(self, item_id, state, error=None):
        self.con.execute("UPDATE work_item SET state=?, error=? WHERE item_id=?",
                         (state, error, item_id))

    def requeue_running(self, run_id) -> int:
        cur = self.con.execute(
            "UPDATE work_item SET state='PENDING' WHERE run_id=? AND state='RUNNING'", (run_id,))
        return cur.rowcount

    def find_resumable_run(self):
        r = self.con.execute(
            "SELECT * FROM processing_run WHERE status IN ('RUNNING','INTERRUPTED')"
            " ORDER BY started_at DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    # ── reads ───────────────────────────────────────────────────────────
    def artifact_by_path(self, rel_path):
        r = self.con.execute("SELECT * FROM artifact WHERE rel_path=? ORDER BY sha256",
                             (rel_path,)).fetchall()
        return [dict(x) for x in r]

    def claims_for_subject(self, subject_id):
        """No lifecycle filter: SUPERSEDED and contradicted claims stay visible."""
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM claim WHERE subject_id=? ORDER BY claim_id", (subject_id,))]

    def counts(self) -> dict:
        q = lambda t: self.con.execute(f"SELECT count(*) c FROM {t}").fetchone()["c"]
        return {t: q(t) for t in ("artifact", "symbol", "reference", "evidence",
                                  "claim", "claim_relation", "diagnostic", "work_item")}

    # ── independent invariant audit ─────────────────────────────────────
    def check_invariants(self) -> list[str]:
        """Verify the database independently of the write path.

        Triggers enforce at write time; this re-checks the whole database, so a
        violation introduced by any means is detectable after the fact.
        """
        v: list[str] = []
        c = self.con

        n = c.execute("SELECT count(*) n FROM claim WHERE json_array_length(evidence_ids)=0").fetchone()["n"]
        if n: v.append(f"{n} claim(s) with no evidence")

        n = c.execute("""SELECT count(*) n FROM claim cl
                         WHERE EXISTS (SELECT 1 FROM json_each(cl.evidence_ids) je
                         WHERE NOT EXISTS (SELECT 1 FROM evidence e WHERE e.evidence_id=je.value))
                      """).fetchone()["n"]
        if n: v.append(f"{n} claim(s) referencing missing evidence")

        n = c.execute("""SELECT count(*) n FROM claim cl WHERE cl.establishment IN ('DERIVED','CONFIRMED')
                         AND NOT EXISTS (SELECT 1 FROM json_each(cl.evidence_ids) je
                             JOIN evidence e ON e.evidence_id=je.value
                             WHERE e.verification_strength IN ('EXACT','REPRODUCIBLE'))
                      """).fetchone()["n"]
        if n: v.append(f"{n} trusted claim(s) without verifiable evidence")

        n = c.execute("SELECT count(*) n FROM evidence e WHERE NOT EXISTS"
                      " (SELECT 1 FROM claim_evidence ce WHERE ce.evidence_id=e.evidence_id)"
                      ).fetchone()["n"]
        if n: v.append(f"{n} dangling evidence row(s) not referenced by any claim")

        n = c.execute(f"SELECT count(*) n FROM claim WHERE predicate IN ({STRUCTURAL_SQL_LIST})"
                      " AND establishment NOT IN ('DERIVED','CONFIRMED')").fetchone()["n"]
        if n: v.append(f"{n} structural claim(s) not deterministically established")

        n = c.execute("SELECT count(*) n FROM claim WHERE establishment='DERIVED'"
                      " AND model_id IS NOT NULL").fetchone()["n"]
        if n: v.append(f"{n} DERIVED claim(s) carrying model identity")

        n = c.execute("SELECT count(*) n FROM reference r JOIN claim cl USING(claim_id)"
                      " WHERE r.resolution='UNRESOLVED' AND cl.object_id IS NOT NULL"
                      ).fetchone()["n"]
        if n: v.append(f"{n} UNRESOLVED reference(s) with a resolved target")

        n = c.execute("SELECT count(*) n FROM reference r"
                      " WHERE NOT EXISTS (SELECT 1 FROM claim cl WHERE cl.claim_id=r.claim_id)"
                      ).fetchone()["n"]
        if n: v.append(f"{n} reference detail row(s) with no claim")

        n = c.execute("SELECT count(*) n FROM artifact WHERE parse_status IN ('FAILED','PARTIAL')"
                      " AND (parse_error IS NULL OR parse_error='')").fetchone()["n"]
        if n: v.append(f"{n} failed artifact(s) with no recorded reason")

        n = c.execute("SELECT count(*) n FROM processing_run WHERE status='RUNNING'").fetchone()["n"]
        if n: v.append(f"{n} run(s) still marked RUNNING (interrupted or in progress)")
        return v
