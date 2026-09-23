"""K-1.1: source-occurrence identity, and artifacts that survive failure.

Two independent defects, tested independently:

  * a qualified name is not an identity, so a parent or a reference subject must
    be chosen by SOURCE OCCURRENCE, never by which duplicate came last;
  * a parseable file must never leave the graph without leaving a record.

Insertion order is tested separately from identity on purpose. Ordering alone
would have converted a foreign-key failure into a silently wrong commit.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kgc.analysis import python_backend
from kgc.analysis.mapper import map_analysis
from kgc.pipeline import CrashPoint, ExtractionFailure, ingest
from kgc.safety import ANALYSED_SUFFIXES, walk_corpus
from kgc.store import Store

# Duplicate qualified names at disjoint spans, three levels deep:
#   m.dup.worker          x2
#   m.dup.worker.value    x2
#   m.dup.worker.shadow   x2  (duplicate local, same occurrence)
DUPLICATES = '''CONDITION = True


def dup():
    if CONDITION:
        def worker():
            value = 1
            shadow = 10
            shadow = 11
            return value
    else:
        def worker():
            value = 2
            shadow = 20
            shadow = 21
            return value
    return worker


class Holder:
    if CONDITION:
        class Inner:
            SETTING = 1
    else:
        class Inner:
            SETTING = 2
'''

# Two same-named functions calling different targets: the CALLS subject must be
# the occurrence the call site sits in.
CALLERS = '''def alpha():
    return 1


def beta():
    return 2


def pick(flag):
    if flag:
        def run():
            return alpha()
    else:
        def run():
            return beta()
    return run
'''


def spans(store, rel=None):
    """(qualified_name, byte_start, byte_end, symbol_id, parent_id) per symbol."""
    q = ("SELECT s.qualified_name qn, s.locator loc, s.symbol_id sid, s.parent_id pid,"
         " a.rel_path FROM symbol s JOIN artifact a USING(artifact_id)")
    args = ()
    if rel:
        q += " WHERE a.rel_path=?"
        args = (rel,)
    out = []
    for r in store.con.execute(q, args):
        p = json.loads(r["loc"])
        out.append((r["qn"], p["byte_start"], p["byte_end"], r["sid"], r["pid"]))
    return out


class _Ingested(unittest.TestCase):
    FILES: dict = {}
    KWARGS: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "corpus"
        for rel, body in cls.FILES.items():
            p = cls.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        cls.store = Store(str(Path(cls.tmp.name) / "kg.sqlite"))
        cls.report = ingest(cls.store, cls.root, **cls.KWARGS)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.tmp.cleanup()


# ── 1. identity ───────────────────────────────────────────────────────────

class TestOccurrenceIdentity(_Ingested):
    FILES = {"dup.py": DUPLICATES}

    def by_qname(self, qn):
        return sorted((s for s in spans(self.store) if s[0] == qn), key=lambda s: s[1])

    def test_the_fixture_really_contains_duplicates(self):
        for qn in ("dup.dup.worker", "dup.dup.worker.value", "dup.Holder.Inner"):
            with self.subTest(qn=qn):
                self.assertEqual(len(self.by_qname(qn)), 2,
                                 "fixture stopped producing duplicate qualified names")

    def test_each_child_points_at_the_occurrence_that_contains_it(self):
        ids = {s[3]: s for s in spans(self.store)}
        checked = 0
        for qn, b0, b1, sid, pid in spans(self.store):
            if pid is None:
                continue
            checked += 1
            _pq, p0, p1, _psid, _ = ids[pid]
            with self.subTest(symbol=qn, span=(b0, b1)):
                self.assertTrue(p0 <= b0 and b1 <= p1,
                                f"parent span ({p0},{p1}) does not contain ({b0},{b1})")
        self.assertGreater(checked, 10)

    def test_duplicate_children_do_not_share_a_parent(self):
        """child A -> parent A and child B -> parent B, never both to B."""
        for child_qn, parent_qn in (("dup.dup.worker.value", "dup.dup.worker"),
                                    ("dup.Holder.Inner.SETTING", "dup.Holder.Inner")):
            with self.subTest(child=child_qn):
                children = self.by_qname(child_qn)
                parents = {s[3]: s for s in self.by_qname(parent_qn)}
                self.assertEqual(len(children), 2)
                self.assertEqual(len(parents), 2)
                got = [c[4] for c in children]
                self.assertEqual(len(set(got)), 2,
                                 "both occurrences were given the same parent")
                for c in children:
                    _pq, p0, p1, _s, _ = parents[c[4]]
                    self.assertTrue(p0 <= c[1] and c[2] <= p1)

    def test_duplicate_locals_in_one_scope_keep_distinct_identities(self):
        shadows = self.by_qname("dup.dup.worker.shadow")
        self.assertEqual(len(shadows), 4, "two scopes x two assignments")
        self.assertEqual(len({s[3] for s in shadows}), 4, "symbol ids collided")
        # each pair belongs to the worker occurrence enclosing it
        workers = {s[3]: s for s in self.by_qname("dup.dup.worker")}
        for s in shadows:
            _q, p0, p1, _sid, _ = workers[s[4]]
            self.assertTrue(p0 <= s[1] and s[2] <= p1)

    def test_containment_audit_passes(self):
        self.assertEqual(self.store.check_invariants(), [])


class TestReferenceSubjectIdentity(_Ingested):
    FILES = {"callers.py": CALLERS}

    def test_each_call_is_attributed_to_the_occurrence_it_sits_in(self):
        rows = [dict(r) for r in self.store.con.execute(
            "SELECT s.qualified_name qn, s.symbol_id sid, s.locator sl, e.locator el,"
            "       cl.object_literal, o.qualified_name target"
            "  FROM claim cl JOIN symbol s ON s.symbol_id = cl.subject_id"
            "  LEFT JOIN symbol o ON o.symbol_id = cl.object_id"
            "  JOIN claim_evidence ce ON ce.claim_id = cl.claim_id"
            "  JOIN evidence e ON e.evidence_id = ce.evidence_id"
            " WHERE cl.predicate='CALLS' AND s.qualified_name='callers.pick.run'")]
        self.assertEqual(len(rows), 2, f"expected one call per run occurrence: {rows}")
        self.assertEqual(len({r["sid"] for r in rows}), 2,
                         "both calls were attributed to the same `run` occurrence")
        targets = set()
        for r in rows:
            sl, el = json.loads(r["sl"]), json.loads(r["el"])
            self.assertTrue(sl["byte_start"] <= el["byte_start"]
                            and el["byte_end"] <= sl["byte_end"],
                            "call site lies outside the subject it was attributed to")
            targets.add(r["target"] or r["object_literal"])
        self.assertEqual(targets, {"callers.alpha", "callers.beta"})

    def test_no_reference_subject_was_dropped(self):
        n = self.store.con.execute(
            "SELECT count(*) FROM diagnostic WHERE code='UNRESOLVED_REFERENCE_SUBJECT'"
        ).fetchone()[0]
        self.assertEqual(n, 0)


class TestAmbiguityIsRefusedNotGuessed(unittest.TestCase):
    """Where no unique occurrence exists, nothing is attached."""

    def test_a_reference_with_no_locatable_subject_is_dropped(self):
        data = CALLERS.encode()
        an = python_backend.analyze("aid", data, "callers")
        from kgc.analysis.interface import RawReference
        from kgc.ir import Resolution
        # a reference claiming a subject that occurs twice, sitting in neither
        an.references.append(RawReference(
            "callers.pick.run", "CALLS", "ghost", None, Resolution.UNRESOLVED,
            an.symbols[0].locator, "synthetic"))
        _syms, claims, _refs, diags = map_analysis(
            an, artifact_id="aid", data=data, run_id="r")
        self.assertTrue(any(d.code == "UNRESOLVED_REFERENCE_SUBJECT" for d in diags),
                        "an unlocatable subject was silently resolved")
        self.assertFalse(any(c.object_literal == "ghost" for c, _ in claims),
                         "a claim was attached to an arbitrary duplicate")

    def test_a_target_defined_twice_in_one_artifact_stays_unresolved(self):
        src = ("def pick():\n    if 1:\n        def run():\n            pass\n"
               "    else:\n        def run():\n            pass\n\n\n"
               "def go():\n    return run()\n")
        data = src.encode()
        an = python_backend.analyze("aid", data, "m")
        from dataclasses import replace
        from kgc.ir import Resolution
        forced = [replace(r, to_qname="m.pick.run", resolution=Resolution.DETERMINISTIC)
                  if r.predicate == "CALLS" and r.to_name == "run" else r
                  for r in an.references]
        _s, claims, refs, _d = map_analysis(an, artifact_id="aid", data=data,
                                            run_id="r", resolved_refs=forced)
        forced_rows = [r for r in refs if r[1] == "run"]
        self.assertTrue(forced_rows)
        for _cid, _name, resolution, reason in forced_rows:
            self.assertEqual(resolution, "UNRESOLVED")
            self.assertIn("definitions in this artifact", reason)
        for claim, _ev in claims:
            if claim.predicate == "CALLS" and claim.object_literal == "run":
                self.assertIsNone(claim.object_id,
                                  "an ambiguous target was bound to one occurrence")


# ── 2. persistence order, tested separately from identity ────────────────

class TestPersistenceOrder(unittest.TestCase):
    def test_parents_are_emitted_before_their_children(self):
        data = DUPLICATES.encode()
        an = python_backend.analyze("aid", data, "dup")
        symbols, _c, _r, _d = map_analysis(an, artifact_id="aid", data=data, run_id="r")
        seen = set()
        for s in symbols:
            if s.parent_id is not None:
                self.assertIn(s.parent_id, seen,
                              f"{s.qualified_name} inserted before its parent")
            seen.add(s.symbol_id)

    def test_the_database_accepts_that_order_with_foreign_keys_on(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "corpus"
            root.mkdir()
            (root / "dup.py").write_text(DUPLICATES, encoding="utf-8")
            s = Store(str(Path(td) / "kg.sqlite"))
            self.assertEqual(
                s.con.execute("PRAGMA foreign_keys").fetchone()[0], 1,
                "the ordering guarantee is meaningless without foreign keys on")
            rep = ingest(s, root)
            self.assertEqual(rep.failed, 0)
            self.assertEqual(s.check_invariants(), [])
            s.close()


# ── 3. a parseable artifact survives an extraction failure ───────────────

class TestExtractionFailureKeepsTheArtifact(_Ingested):
    FILES = {"good.py": "def ok():\n    return 1\n",
             "bad.py": "def also_parses():\n    return 2\n"}
    KWARGS = {"fail_extraction_at": "bad.py"}

    def row(self, rel):
        r = self.store.con.execute(
            "SELECT * FROM artifact WHERE rel_path=?", (rel,)).fetchone()
        return dict(r) if r else None

    def counts_for(self, rel):
        c = self.store.con
        art = self.row(rel)
        aid = art["artifact_id"]
        return {
            "symbol": c.execute("SELECT count(*) FROM symbol WHERE artifact_id=?",
                                (aid,)).fetchone()[0],
            "evidence": c.execute("SELECT count(*) FROM evidence WHERE artifact_id=?",
                                  (aid,)).fetchone()[0],
            "claim": c.execute("SELECT count(*) FROM claim cl JOIN symbol s"
                               " ON s.symbol_id=cl.subject_id WHERE s.artifact_id=?",
                               (aid,)).fetchone()[0],
            "diagnostic": c.execute("SELECT count(*) FROM diagnostic WHERE artifact_id=?",
                                    (aid,)).fetchone()[0],
        }

    def test_the_artifact_still_exists(self):
        self.assertIsNotNone(self.row("bad.py"),
                             "a parseable file vanished from the graph")

    def test_it_is_marked_failed_with_a_reason(self):
        r = self.row("bad.py")
        self.assertEqual(r["parse_status"], "FAILED")
        self.assertTrue(r["parse_error"])
        self.assertIn("EXTRACTION_FAILED", r["parse_error"])

    def test_a_diagnostic_explains_it(self):
        self.assertGreaterEqual(self.counts_for("bad.py")["diagnostic"], 1)
        codes = {r[0] for r in self.store.con.execute(
            "SELECT code FROM diagnostic d JOIN artifact a USING(artifact_id)"
            " WHERE a.rel_path='bad.py'")}
        self.assertIn("EXTRACTION_FAILED", codes)

    def test_it_left_no_partial_graph(self):
        c = self.counts_for("bad.py")
        self.assertEqual((c["symbol"], c["claim"], c["evidence"]), (0, 0, 0))

    def test_the_work_item_records_the_failure(self):
        rows = [dict(r) for r in self.store.con.execute(
            "SELECT state, error FROM work_item WHERE target='bad.py'")]
        self.assertTrue(any(r["state"] == "FAILED" and r["error"] for r in rows), rows)

    def test_the_healthy_artifact_is_untouched(self):
        self.assertEqual(self.row("good.py")["parse_status"], "OK")
        self.assertGreater(self.counts_for("good.py")["symbol"], 0)
        self.assertGreater(self.counts_for("good.py")["claim"], 0)

    def test_no_invariant_is_violated(self):
        self.assertEqual(self.store.check_invariants(), [])

    def test_coverage_still_complete(self):
        self.assertEqual(self.report.absent, [])
        self.assertEqual(self.report.extraction_failed, 1)


class TestResolutionFailureAlsoKeepsTheArtifact(_Ingested):
    """Stage 2 fails AFTER stage 1 committed symbols. The artifact survives and
    the symbols go with the claims -- a FAILED artifact keeps no half-graph."""
    FILES = {"good.py": "def ok():\n    return 1\n",
             "bad.py": "class C:\n    def m(self):\n        return ok()\n"}
    KWARGS = {"fail_resolution_at": "bad.py"}

    def test_the_artifact_survives_marked_failed(self):
        r = dict(self.store.con.execute(
            "SELECT * FROM artifact WHERE rel_path='bad.py'").fetchone())
        self.assertEqual(r["parse_status"], "FAILED")
        self.assertIn("EXTRACTION_FAILED", r["parse_error"])

    def test_stage_one_symbols_were_purged_with_the_claims(self):
        aid = self.store.con.execute(
            "SELECT artifact_id FROM artifact WHERE rel_path='bad.py'").fetchone()[0]
        c = self.store.con
        self.assertEqual(c.execute("SELECT count(*) FROM symbol WHERE artifact_id=?",
                                   (aid,)).fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT count(*) FROM evidence WHERE artifact_id=?",
                                   (aid,)).fetchone()[0], 0)

    def test_the_other_artifact_kept_its_graph(self):
        n = self.store.con.execute(
            "SELECT count(*) FROM claim cl JOIN symbol s ON s.symbol_id=cl.subject_id"
            " JOIN artifact a USING(artifact_id) WHERE a.rel_path='good.py'").fetchone()[0]
        self.assertGreater(n, 0)

    def test_coverage_and_invariants_hold(self):
        self.assertEqual(self.report.absent, [])
        self.assertEqual(self.store.check_invariants(), [])


class TestCrashIsNotAnExtractionFailure(unittest.TestCase):
    """§8: a process crash writes nothing. Do not convert one into the other."""

    def test_a_crash_leaves_no_artifact_and_no_failed_marker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "corpus"
            root.mkdir()
            (root / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
            s = Store(str(Path(td) / "kg.sqlite"))
            with self.assertRaises(CrashPoint):
                ingest(s, root, crash_at=("during_relationship", 1))
            self.assertEqual(
                s.con.execute("SELECT count(*) FROM artifact").fetchone()[0], 0,
                "a simulated crash recorded a FAILED artifact instead of losing the "
                "transaction; the crash/resume semantics were weakened")
            self.assertEqual(
                s.con.execute("SELECT state FROM work_item").fetchone()[0], "RUNNING",
                "resume can only requeue an item left RUNNING")
            s.close()

    def test_the_two_failure_kinds_are_distinct_exceptions(self):
        self.assertFalse(issubclass(ExtractionFailure, CrashPoint))
        self.assertFalse(issubclass(CrashPoint, ExtractionFailure))


# ── 4. coverage: no walked analysable file may be absent ─────────────────

class TestArtifactCoverage(unittest.TestCase):
    CORPORA = ("eval/corpus", "eval/corpus2")

    def test_every_walked_analysable_file_has_an_artifact(self):
        repo = Path(__file__).resolve().parents[1]
        for rel in self.CORPORA:
            root = (repo / rel).resolve()
            if not root.exists():
                self.skipTest(f"{rel} not present")
            with self.subTest(corpus=rel), tempfile.TemporaryDirectory() as td:
                s = Store(str(Path(td) / "kg.sqlite"))
                rep = ingest(s, root)
                walked = {str(p.relative_to(root)) for p in walk_corpus(root)
                          if p.suffix in ANALYSED_SUFFIXES}
                stored = {r[0] for r in s.con.execute("SELECT rel_path FROM artifact")}
                self.assertEqual(walked - stored, set(), "walked file with no artifact row")
                self.assertEqual(rep.absent, [])
                self.assertEqual(rep.failed, 0)
                s.close()

    def test_every_failed_artifact_carries_a_reason_and_a_diagnostic(self):
        """§14. Exercised against a corpus that actually contains a failure."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "corpus"
            root.mkdir()
            (root / "ok.py").write_text("def a():\n    return 1\n", encoding="utf-8")
            (root / "boom.py").write_text("def b():\n    return 2\n", encoding="utf-8")
            (root / "broken.py").write_text("def f(:\n", encoding="utf-8")
            s = Store(str(Path(td) / "kg.sqlite"))
            rep = ingest(s, root, fail_extraction_at="boom.py")
            self.assertEqual(rep.absent, [])
            failed = [dict(r) for r in s.con.execute(
                "SELECT artifact_id, rel_path, parse_error FROM artifact"
                " WHERE parse_status='FAILED'")]
            self.assertEqual({r["rel_path"] for r in failed}, {"boom.py", "broken.py"})
            for r in failed:
                with self.subTest(rel=r["rel_path"]):
                    self.assertTrue(r["parse_error"])
                    self.assertGreaterEqual(s.con.execute(
                        "SELECT count(*) FROM diagnostic WHERE artifact_id=?",
                        (r["artifact_id"],)).fetchone()[0], 1)
            ok = s.con.execute(
                "SELECT count(*) FROM artifact a JOIN diagnostic d USING(artifact_id)"
                " WHERE a.parse_status='OK' AND d.code='EXTRACTION_FAILED'").fetchone()[0]
            self.assertEqual(ok, 0, "an OK artifact carries an extraction-failure state")
            self.assertEqual(s.check_invariants(), [])
            s.close()


if __name__ == "__main__":
    unittest.main()
