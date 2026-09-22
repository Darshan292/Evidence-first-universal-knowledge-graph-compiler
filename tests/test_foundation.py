"""Gate 1 acceptance suite. Stdlib unittest only -- no test dependencies."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kgc import SCHEMA_VERSION
from kgc.evidence import EvidenceError, make_evidence, verify
from kgc.ids import claim_id, content_sha256, entity_id
from kgc.ir import (Claim, Establishment, Lifecycle, Locator, LocatorKind,
                    ParseStatus, Resolution)
from kgc.pipeline import CrashPoint, ingest
from kgc.safety import (UnsafePath, classify, parse_xml_hardened, resolve_within,
                        xml_itertext)
from kgc.store import Store
from tests import fixtures


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = fixtures.build(Path(self.tmp.name) / "corpus")
        self.db = str(Path(self.tmp.name) / "kg.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def fresh(self):
        s = Store(self.db)
        self.addCleanup(s.close)
        return s

    @staticmethod
    def snapshot(db) -> dict:
        """Compare as SETS: row insertion order may legitimately differ."""
        con = sqlite3.connect(db); con.row_factory = sqlite3.Row
        out = {}
        for t, cols in (("artifact", "artifact_id,sha256,parse_status,parse_error"),
                        ("symbol", "symbol_id,qualified_name,kind,locator"),
                        ("claim", "claim_id,predicate,subject_id,object_id,establishment,lifecycle"),
                        ("evidence", "evidence_id,quoted_text,verification_strength"),
                        ("reference", "claim_id,to_name,resolution"),
                        ("diagnostic", "diagnostic_id,code,message")):
            out[t] = {tuple(r) for r in con.execute(f"SELECT {cols} FROM {t}")}
        con.close()
        return out


class TestReproducibility(Base):
    def test_two_independent_runs_produce_equivalent_state(self):
        db2 = str(Path(self.tmp.name) / "kg2.sqlite")
        s1 = Store(self.db); ingest(s1, self.root); s1.close()
        s2 = Store(db2); ingest(s2, self.root); s2.close()
        a, b = self.snapshot(self.db), self.snapshot(db2)
        for table in a:
            self.assertEqual(a[table], b[table],
                             f"{table} differs between two independent runs")

    def test_run_ids_are_intentionally_different(self):
        db2 = str(Path(self.tmp.name) / "kg2.sqlite")
        s1 = Store(self.db); r1 = ingest(s1, self.root); s1.close()
        s2 = Store(db2); r2 = ingest(s2, self.root); s2.close()
        self.assertNotEqual(r1.run_id, r2.run_id,
                            "run_id must be unique so runs are distinguishable")


class TestIdempotency(Base):
    def test_reindex_does_not_duplicate(self):
        s = self.fresh()
        ingest(s, self.root)
        first = s.counts()
        ingest(s, self.root)
        ingest(s, self.root)
        self.assertEqual(first, s.counts(), "re-indexing duplicated semantic facts")
        self.assertEqual(s.check_invariants(), [])


class TestDeterministicIds(Base):
    def test_same_inputs_same_ids(self):
        db2 = str(Path(self.tmp.name) / "kg2.sqlite")
        s1 = Store(self.db); ingest(s1, self.root); s1.close()
        s2 = Store(db2); ingest(s2, self.root); s2.close()
        self.assertEqual(self.snapshot(self.db)["symbol"], self.snapshot(db2)["symbol"])

    def test_entity_id_is_run_independent(self):
        self.assertEqual(entity_id("Service", "payment service"),
                         entity_id("Service", "payment service"))

    def test_model_identity_participates_in_claim_id(self):
        kw = dict(predicate="MENTIONS", subject_id="s", object_id=None,
                  object_literal="x", extractor_id="e", extractor_version="1",
                  schema_version="1", evidence_ids=["e1"])
        a = claim_id(model_id=None, prompt_version=None, **kw)
        b = claim_id(model_id="llama3.1:8b", prompt_version="p1", **kw)
        c = claim_id(model_id="qwen2.5:7b", prompt_version="p1", **kw)
        self.assertNotEqual(a, b, "model claim must not collide with derived claim")
        self.assertNotEqual(b, c, "different models must not overwrite one another")

    def test_evidence_order_does_not_change_claim_id(self):
        kw = dict(predicate="P", subject_id="s", object_id="o", object_literal=None,
                  extractor_id="e", extractor_version="1", model_id=None,
                  prompt_version=None, schema_version="1")
        self.assertEqual(claim_id(evidence_ids=["a", "b"], **kw),
                         claim_id(evidence_ids=["b", "a"], **kw))


class TestEvidence(Base):
    def test_every_trusted_claim_resolves_to_valid_evidence(self):
        s = self.fresh(); ingest(s, self.root)
        rows = s.con.execute(
            "SELECT cl.claim_id, cl.establishment, e.evidence_id, e.quoted_text,"
            "       e.verification_strength, e.locator, a.rel_path, a.sha256"
            "  FROM claim cl JOIN claim_evidence ce USING(claim_id)"
            "  JOIN evidence e USING(evidence_id) JOIN artifact a ON a.artifact_id=e.artifact_id"
            " WHERE cl.establishment IN ('DERIVED','CONFIRMED')").fetchall()
        total_trusted = s.con.execute(
            "SELECT count(*) c FROM claim WHERE establishment IN ('DERIVED','CONFIRMED')"
        ).fetchone()["c"]
        self.assertGreater(total_trusted, 15)
        self.assertGreaterEqual(len(rows), total_trusted,
                                "a trusted claim has no linked evidence row")
        for r in rows:
            self.assertIn(r["verification_strength"], ("EXACT", "REPRODUCIBLE"))
            data = (self.root / r["rel_path"]).read_bytes()
            self.assertEqual(content_sha256(data), r["sha256"])
            loc = json.loads(r["locator"])
            actual = data[loc["byte_start"]:loc["byte_end"]].decode("utf-8")
            self.assertEqual(actual, r["quoted_text"],
                             f"evidence does not re-derive from source: {r['rel_path']}")

    def test_fabricated_evidence_is_rejected_not_repaired(self):
        data = b"PAYMENT_TIMEOUT_SECONDS = 30\n"
        loc = Locator(LocatorKind.BYTE_RANGE,
                      {"byte_start": 0, "byte_end": 23, "line_start": 1, "line_end": 1})
        good = verify(make_evidence("a", data, loc, "PAYMENT_TIMEOUT_SECONDS"), data)
        self.assertEqual(good.verification_strength.value, "EXACT")
        bad = make_evidence("a", data, loc, "PAYMENT_TIMEOUT_MINUTES")
        with self.assertRaises(EvidenceError):
            verify(bad, data)

    def test_source_drift_is_detected(self):
        data = b"X = 1\n"
        loc = Locator(LocatorKind.BYTE_RANGE,
                      {"byte_start": 0, "byte_end": 1, "line_start": 1, "line_end": 1})
        ev = verify(make_evidence("a", data, loc, "X"), data)
        with self.assertRaises(EvidenceError):
            verify(ev, b"Y = 2\n")

    def test_ast_node_locator_verifies_structurally(self):
        """AST identity survives reformatting, which a byte range does not."""
        data = b"class A:\n    def m(self):\n        return 1\n"
        loc = Locator(LocatorKind.AST_NODE,
                      {"path": "ClassDef:A/FunctionDef:m", "node_type": "FunctionDef"})
        ev = verify(make_evidence("a", data, loc, "m"), data)
        self.assertEqual(ev.verification_strength.value, "EXACT")

        reformatted = b"class A:\n\n    def m(self):\n        return 1\n"
        moved = make_evidence("a", reformatted, loc, "m")
        self.assertEqual(verify(moved, reformatted).verification_strength.value, "EXACT")

        gone = make_evidence("a", b"class A:\n    pass\n", loc, "m")
        with self.assertRaises(EvidenceError):
            verify(gone, b"class A:\n    pass\n")

    def test_json_pointer_locator_verifies_and_rejects(self):
        data = b'{"service": {"timeouts": [30, 60]}}'
        loc = Locator(LocatorKind.JSON_POINTER, {"pointer": "/service/timeouts/0"})
        ev = verify(make_evidence("a", data, loc, "30"), data)
        self.assertEqual(ev.verification_strength.value, "EXACT")
        wrong = make_evidence("a", data, loc, "99")
        with self.assertRaises(EvidenceError):
            verify(wrong, data)
        missing = make_evidence(
            "a", data, Locator(LocatorKind.JSON_POINTER, {"pointer": "/nope"}), "x")
        with self.assertRaises(EvidenceError):
            verify(missing, data)

    def test_unsupported_locator_kind_refuses_verification(self):
        with self.assertRaises(ValueError):
            Locator(LocatorKind.PDF_BOX, {"page": 1})


class TestEstablishment(Base):
    """A model or heuristic may never establish a parser-level structural fact."""

    def _mk(self, s, predicate, establishment, model_id=None, strength="EXACT"):
        run = s.con.execute("SELECT run_id FROM processing_run LIMIT 1").fetchone()[0]
        art = s.con.execute("SELECT artifact_id, sha256 FROM artifact WHERE sha256!='' LIMIT 1").fetchone()
        eid = f"ev-{predicate}-{establishment}-{model_id}"
        s.con.execute("INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?,?)",
                      (eid, art["artifact_id"], art["sha256"], "byte_range",
                       '{"byte_start":0,"byte_end":1}', "x", strength, "t", "now"))
        cid = f"cl-{predicate}-{establishment}-{model_id}-{strength}"
        s.con.execute(
            "INSERT INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, predicate, "subj", "symbol", None, None, "lit",
             "ACTIVE", establishment, 0.92, "x", "1", model_id, None,
             SCHEMA_VERSION, run, json.dumps([eid]), None))
        return cid

    def test_model_cannot_establish_structural_predicate(self):
        s = self.fresh(); ingest(s, self.root)
        for est in ("PROPOSED", "DISPUTED"):
            with self.subTest(est=est), self.assertRaises(sqlite3.IntegrityError) as cm:
                self._mk(s, "CALLS", est, model_id="llama3.1:8b")
            self.assertIn("structural predicate", str(cm.exception))

    def test_high_confidence_does_not_imply_verified(self):
        """confidence=0.92 must not substitute for evidence."""
        s = self.fresh(); ingest(s, self.root)
        with self.assertRaises(sqlite3.IntegrityError):
            self._mk(s, "CALLS", "PROPOSED", model_id="m")   # 0.92 confidence set

    def test_structural_only_evidence_cannot_confirm(self):
        s = self.fresh(); ingest(s, self.root)
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self._mk(s, "MENTIONS", "CONFIRMED", strength="STRUCTURAL")
        self.assertIn("EXACT/REPRODUCIBLE", str(cm.exception))

    def test_derived_claim_may_not_carry_model_identity(self):
        s = self.fresh(); ingest(s, self.root)
        with self.assertRaises(sqlite3.IntegrityError):
            self._mk(s, "MENTIONS", "DERIVED", model_id="llama3.1:8b")

    def test_claim_without_evidence_is_rejected(self):
        s = self.fresh(); ingest(s, self.root)
        run = s.con.execute("SELECT run_id FROM processing_run LIMIT 1").fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            s.con.execute("INSERT INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          ("no-ev", "MENTIONS", "s", "symbol", None, None, "x",
                           "ACTIVE", "PROPOSED", None, "x", "1", "m", None,
                           SCHEMA_VERSION, run, "[]", None))
        self.assertIn("INVARIANT", str(cm.exception))


class TestContradiction(Base):
    """A contradicted claim must NEVER disappear merely because it conflicts."""

    def test_contradicted_and_superseded_claims_remain_queryable(self):
        s = self.fresh(); ingest(s, self.root)
        rows = s.con.execute(
            "SELECT claim_id, subject_id FROM claim WHERE object_id IS NOT NULL LIMIT 2").fetchall()
        a, b = rows[0], rows[1]
        run = s.con.execute("SELECT run_id FROM processing_run LIMIT 1").fetchone()[0]
        s.begin()
        s.add_claim_relation(a["claim_id"], b["claim_id"], "CONTRADICTS", "test", run)
        s.con.execute("UPDATE claim SET lifecycle='SUPERSEDED' WHERE claim_id=?", (b["claim_id"],))
        s.commit()

        visible = {c["claim_id"] for c in s.claims_for_subject(a["subject_id"])}
        self.assertIn(a["claim_id"], visible, "contradicted claim vanished from queries")
        superseded = {c["claim_id"] for c in s.claims_for_subject(b["subject_id"])}
        self.assertIn(b["claim_id"], superseded, "superseded claim vanished from queries")
        self.assertEqual(s.check_invariants(), [])

    def test_no_default_query_path_filters_on_lifecycle(self):
        src = Path(__file__).resolve().parents[1] / "kgc" / "store.py"
        body = src.read_text()
        start = body.index("# ── reads ")
        end = body.index("# ── independent invariant audit")
        self.assertNotIn("lifecycle=", body[start:end],
                         "a read path filters on lifecycle -- this is how defect D-3 happened")


class TestParserFailure(Base):
    def test_malformed_file_is_explicit_failure_not_empty_graph(self):
        s = self.fresh(); rep = ingest(s, self.root)
        row = s.con.execute(
            "SELECT * FROM artifact WHERE rel_path='broken.py'").fetchone()
        self.assertIsNotNone(row, "malformed file must still produce an artifact row")
        self.assertEqual(row["parse_status"], "FAILED")
        self.assertTrue(row["parse_error"])
        diags = s.con.execute("SELECT * FROM diagnostic WHERE artifact_id=? AND code='SYNTAX_ERROR'",
                              (row["artifact_id"],)).fetchall()
        self.assertTrue(diags, "syntax error produced no diagnostic")
        self.assertEqual(rep.failed, 1)

    def test_empty_file_is_distinguishable_from_malformed(self):
        s = self.fresh(); ingest(s, self.root)
        empty = s.con.execute("SELECT * FROM artifact WHERE rel_path='empty.py'").fetchone()
        broken = s.con.execute("SELECT * FROM artifact WHERE rel_path='broken.py'").fetchone()
        self.assertEqual(empty["parse_status"], "OK")
        self.assertEqual(broken["parse_status"], "FAILED")
        self.assertNotEqual(empty["parse_status"], broken["parse_status"])

    def test_unresolved_references_are_recorded_not_omitted(self):
        s = self.fresh(); ingest(s, self.root)
        n = s.con.execute("SELECT count(*) c FROM reference WHERE resolution='UNRESOLVED'").fetchone()["c"]
        self.assertGreater(n, 0, "dynamic dispatch produced no UNRESOLVED record")
        bad = s.con.execute(
            "SELECT count(*) c FROM reference r JOIN claim cl USING(claim_id)"
            " WHERE r.resolution='UNRESOLVED' AND cl.object_id IS NOT NULL").fetchone()["c"]
        self.assertEqual(bad, 0, "an UNRESOLVED reference was given a resolved target")

    def test_unicode_identifiers_produce_correct_byte_offsets(self):
        s = self.fresh(); ingest(s, self.root)
        art = s.con.execute("SELECT * FROM artifact WHERE rel_path='unicode_names.py'").fetchone()
        self.assertEqual(art["parse_status"], "OK")
        data = (self.root / "unicode_names.py").read_bytes()
        for e in s.con.execute("SELECT * FROM evidence WHERE artifact_id=?", (art["artifact_id"],)):
            loc = json.loads(e["locator"])
            self.assertEqual(data[loc["byte_start"]:loc["byte_end"]].decode("utf-8"),
                             e["quoted_text"])


class TestSecurity(Base):
    def test_path_traversal_rejected(self):
        with self.assertRaises(UnsafePath):
            resolve_within(self.root, Path("../../etc/passwd"))

    def test_absolute_path_outside_root_rejected(self):
        with self.assertRaises(UnsafePath):
            resolve_within(self.root, Path("/etc/passwd"))

    def test_symlink_escape_rejected(self):
        link = self.root / "escape.py"
        try:
            os.symlink("/etc/passwd", link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        data, rej = classify(link, root=self.root)
        self.assertIsNone(data)
        self.assertIn(rej.code, ("SYMLINK_REFUSED", "PATH_UNSAFE", "NOT_A_FILE"))

    def test_binary_masquerading_as_text_rejected(self):
        p = self.root / "fake.py"
        p.write_bytes(b"\x7fELF\x00\x00\x00\x00import os\n")
        data, rej = classify(p, root=self.root)
        self.assertIsNone(data)
        self.assertEqual(rej.code, "BINARY_AS_TEXT")

    def test_oversized_file_rejected_with_reason(self):
        from kgc import safety
        p = self.root / "huge.py"
        p.write_bytes(b"x = 1\n" * 10)
        orig = safety.MAX_FILE_BYTES
        safety.MAX_FILE_BYTES = 10
        try:
            data, rej = classify(p, root=self.root)
        finally:
            safety.MAX_FILE_BYTES = orig
        self.assertIsNone(data)
        self.assertEqual(rej.code, "OVERSIZE")

    def test_rejections_are_recorded_not_silently_skipped(self):
        (self.root / "fake2.py").write_bytes(b"\x00\x01binary")
        s = self.fresh(); rep = ingest(s, self.root)
        self.assertTrue(rep.rejections)
        row = s.con.execute("SELECT * FROM artifact WHERE rel_path='fake2.py'").fetchone()
        self.assertEqual(row["parse_status"], "SKIPPED")
        self.assertIn("BINARY_AS_TEXT", row["parse_error"])

    def test_xxe_is_inert_in_evidence_verification_path(self):
        secret = Path(self.tmp.name) / "secret.txt"
        secret.write_text("TOP_SECRET_CONTENT")
        payload = (f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM '
                   f'"file://{secret}">]><r>&x;</r>').encode()
        with self.assertRaises(ValueError) as cm:
            parse_xml_hardened(payload)
        self.assertIn("DOCTYPE", str(cm.exception))

    def test_billion_laughs_rejected(self):
        payload = (b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aa">'
                   b'<!ENTITY b "&a;&a;&a;&a;">]><l>&b;</l>')
        with self.assertRaises(ValueError):
            parse_xml_hardened(payload)

    def test_benign_xml_still_parses(self):
        root = parse_xml_hardened(b"<root><item>ok</item></root>")
        self.assertEqual(xml_itertext(root).strip(), "ok")

    def test_injection_text_is_data_not_instruction(self):
        """Hostile comments must become ordinary content with no special power."""
        s = self.fresh(); ingest(s, self.root)
        art = s.con.execute("SELECT * FROM artifact WHERE rel_path='injected.py'").fetchone()
        self.assertEqual(art["parse_status"], "OK")
        claims = s.con.execute(
            "SELECT * FROM claim cl JOIN claim_evidence ce USING(claim_id)"
            " JOIN evidence e USING(evidence_id) WHERE e.artifact_id=?",
            (art["artifact_id"],)).fetchall()
        for c in claims:
            self.assertEqual(c["establishment"], "DERIVED")
            self.assertIsNone(c["model_id"])
        self.assertEqual(s.check_invariants(), [])


class TestCrashRecovery(Base):
    POINTS = ["before_insert", "during_entity", "during_relationship",
              "during_evidence", "before_commit", "after_commit"]

    def test_crash_at_each_point_then_resume(self):
        reference_db = str(Path(self.tmp.name) / "ref.sqlite")
        s = Store(reference_db); ingest(s, self.root); s.close()
        expected = self.snapshot(reference_db)

        for point in self.POINTS:
            with self.subTest(point=point):
                db = str(Path(self.tmp.name) / f"crash_{point}.sqlite")
                s = Store(db)
                with self.assertRaises(CrashPoint):
                    ingest(s, self.root, crash_at=(point, 2))
                # never corrupt, even mid-run
                self.assertNotIn("claim(s) with no evidence", " ".join(s.check_invariants()))
                s.close()

                s = Store(db)                 # simulate restart
                ingest(s, self.root)
                got = self.snapshot(db)
                violations = [v for v in s.check_invariants() if "RUNNING" not in v]
                s.close()
                self.assertEqual(violations, [], f"corrupt state after crash at {point}")
                for table in ("artifact", "symbol", "claim", "evidence", "reference"):
                    self.assertEqual(got[table], expected[table],
                                     f"resume after {point} diverged in {table}")

    def test_interrupted_run_is_resumable_and_requeues(self):
        db = str(Path(self.tmp.name) / "r.sqlite")
        s = Store(db)
        # NOTE: artifact #1 is broken.py, which returns early on the FAILED path
        # and never reaches a crash point. Targeting it made this test vacuous.
        # Index 3 is a file that reaches the full write sequence.
        with self.assertRaises(CrashPoint):
            ingest(s, self.root, crash_at=("during_evidence", 3))
        stuck = s.con.execute("SELECT count(*) c FROM work_item WHERE state='RUNNING'").fetchone()["c"]
        self.assertEqual(stuck, 1, "RUNNING state must survive a crash so resume can requeue it")
        attempts = s.con.execute(
            "SELECT max(attempts) a FROM work_item").fetchone()["a"]
        self.assertEqual(attempts, 1, "attempts must be durable across a crash")
        ingest(s, self.root)
        self.assertEqual(s.con.execute(
            "SELECT count(*) c FROM work_item WHERE state='RUNNING'").fetchone()["c"], 0)
        s.close()

    def test_repeatedly_crashing_item_is_quarantined_not_retried_forever(self):
        db = str(Path(self.tmp.name) / "q.sqlite")
        s = Store(db)
        from kgc.pipeline import MAX_ATTEMPTS
        for _ in range(MAX_ATTEMPTS):
            with self.assertRaises(CrashPoint):
                ingest(s, self.root, crash_at=("during_evidence", 3))
        ingest(s, self.root)   # must terminate, not loop
        quarantined = s.con.execute(
            "SELECT count(*) c FROM work_item WHERE state='FAILED'").fetchone()["c"]
        self.assertGreaterEqual(quarantined, 1, "no retry bound on a crashing item")
        s.close()


class TestVersioning(Base):
    def test_changed_content_creates_new_version_preserving_provenance(self):
        s = self.fresh()
        ingest(s, self.root)
        before = s.artifact_by_path("config.py")
        self.assertEqual(len(before), 1)

        (self.root / "config.py").write_text(
            '"""Configuration."""\nPAYMENT_TIMEOUT_SECONDS = 60\nRETRY_LIMIT = 3\n')
        ingest(s, self.root)
        after = s.artifact_by_path("config.py")

        self.assertEqual(len(after), 2, "changed content must create a new artifact version")
        self.assertNotEqual(after[0]["sha256"], after[1]["sha256"])
        self.assertIn(before[0]["artifact_id"], {a["artifact_id"] for a in after},
                      "the prior version must remain queryable")

        old_ev = s.con.execute(
            "SELECT count(*) c FROM evidence WHERE artifact_sha256=?",
            (before[0]["sha256"],)).fetchone()["c"]
        self.assertGreater(old_ev, 0, "evidence for the prior version was destroyed")
        self.assertEqual(s.check_invariants(), [])

    def test_provenance_explains_why_a_fact_exists(self):
        s = self.fresh(); ingest(s, self.root)
        row = s.con.execute(
            "SELECT cl.*, r.software_version, r.config_hash, r.schema_version"
            "  FROM claim cl JOIN processing_run r USING(run_id) LIMIT 1").fetchone()
        for field in ("extractor_id", "extractor_version", "schema_version",
                      "software_version", "config_hash", "run_id"):
            self.assertTrue(row[field], f"provenance field {field} is empty")


class TestDeterministicOnlyMode(Base):
    def test_useful_with_zero_llm_involvement(self):
        s = self.fresh(); rep = ingest(s, self.root)
        names = {r["qualified_name"] for r in s.con.execute("SELECT qualified_name FROM symbol")}
        for expected in ("config.PAYMENT_TIMEOUT_SECONDS", "payment.PaymentService",
                         "payment.PaymentService.charge", "payment.make_service"):
            self.assertIn(expected, names, "deterministic extraction missed a known symbol")
        # Assert specific known facts rather than a magic count: this proves the
        # deterministic layer establishes real structure, not merely many rows.
        def fact(pred, subj_qn, obj_qn):
            return s.con.execute(
                "SELECT cl.claim_id, cl.establishment FROM claim cl"
                "  JOIN symbol s1 ON s1.symbol_id = cl.subject_id"
                "  JOIN symbol s2 ON s2.symbol_id = cl.object_id"
                " WHERE cl.predicate=? AND s1.qualified_name=? AND s2.qualified_name=?",
                (pred, subj_qn, obj_qn)).fetchone()

        called = fact("CALLS", "payment.make_service", "payment.PaymentService")
        self.assertIsNotNone(called, "deterministic cross-scope call was not established")
        self.assertEqual(called["establishment"], "DERIVED")

        contains = fact("CONTAINS", "payment.PaymentService", "payment.PaymentService.charge")
        self.assertIsNotNone(contains, "class->method containment was not established")

        # the import statement is a deterministic observation even though the
        # target symbol lives in another artifact
        imp = s.con.execute(
            "SELECT r.resolution FROM reference r JOIN claim cl USING(claim_id)"
            " WHERE cl.predicate='IMPORTS' AND r.to_name LIKE '%PAYMENT_TIMEOUT_SECONDS%'"
        ).fetchone()
        self.assertIsNotNone(imp)
        self.assertEqual(imp["resolution"], "HEURISTIC")

        # dynamic dispatch must remain explicitly unresolved, never guessed
        unres = s.con.execute(
            "SELECT count(*) c FROM reference WHERE resolution='UNRESOLVED'"
            " AND to_name LIKE '%handler%'").fetchone()["c"]
        self.assertGreater(unres, 0, "dynamic dispatch was not recorded as UNRESOLVED")
        n_model = s.con.execute(
            "SELECT count(*) c FROM claim WHERE model_id IS NOT NULL").fetchone()["c"]
        self.assertEqual(n_model, 0, "deterministic run produced model-derived claims")
        est = dict(s.con.execute(
            "SELECT establishment, count(*) FROM claim GROUP BY establishment").fetchall())
        self.assertEqual(set(est), {"DERIVED"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
