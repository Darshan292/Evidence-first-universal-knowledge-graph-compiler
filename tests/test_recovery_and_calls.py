"""K-1.2: FAILED-artifact recovery, and CALLS that a decorator did not make.

Two integrity fixes with nothing in common except that both were found by
reproducing a claimed guarantee instead of trusting it.

  * a FAILED artifact must be able to become OK again when re-ingestion
    succeeds, and must not drag a stale graph along in either direction;
  * `CALLS` means the subject executes the call. A decorator expression runs
    while the definition is built, so it is not a call the function makes.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kgc.analysis import python_backend
from kgc.pipeline import ingest
from kgc.store import Store

# bad.py is the artifact that fails; good.py both calls INTO it and is called
# FROM it, so a purge that ignored inbound edges would dangle good.py's target.
CORPUS = {
    "__init__.py": "",
    "bad.py": "def target():\n    return 1\n",
    # several callers, spread either side of bad.py in walk order, so at least
    # one is resolved AFTER the purge and would emit an unresolved variant
    "aaa_early.py": "from bad import target\n\n\ndef early():\n    return target()\n",
    "good.py": "from bad import target\n\n\ndef caller():\n    return target()\n",
    "zzz_late.py": "from bad import target\n\n\ndef late():\n    return target()\n",
}

DECORATED = '''import app


@app.route("/")
@cache(ttl=60)
def decorated():
    return app.helper()


@app.route("/x")
def calls_the_same_thing():
    app.route("/inner")
    return 1


def plain():
    return app.route("/plain")


class C:
    @property
    def p(self):
        return 1
'''


class _Corpus(unittest.TestCase):
    FILES = CORPUS

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "corpus"
        for rel, body in self.FILES.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        self.store = Store(str(Path(self.tmp.name) / "kg.sqlite"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def artifact(self, rel):
        r = self.store.con.execute(
            "SELECT * FROM artifact WHERE rel_path=?", (rel,)).fetchone()
        return dict(r) if r else None

    def claim_rows(self):
        """Every claim as content, so a stale duplicate is visible."""
        return {tuple(r) for r in self.store.con.execute(
            "SELECT claim_id, predicate, subject_id, object_id, object_literal FROM claim")}

    def graph(self, rel):
        a = self.artifact(rel)["artifact_id"]
        q = lambda sql: self.store.con.execute(sql, (a,)).fetchone()[0]
        return {
            "symbols": q("SELECT count(*) FROM symbol WHERE artifact_id=?"),
            "evidence": q("SELECT count(*) FROM evidence WHERE artifact_id=?"),
            "claims": q("SELECT count(*) FROM claim cl JOIN symbol s"
                        " ON s.symbol_id=cl.subject_id WHERE s.artifact_id=?"),
        }


class _RecoveryTests:
    """Shared body, mixed into one case per stage. Not a TestCase itself, so
    unittest does not collect it with an empty KW."""
    KW = ""

    def assertFailedCleanly(self):
        a = self.artifact("bad.py")
        self.assertEqual(a["parse_status"], "FAILED")
        self.assertIn("EXTRACTION_FAILED", a["parse_error"])
        self.assertEqual(self.graph("bad.py"), {"symbols": 0, "evidence": 0, "claims": 0})
        self.assertEqual(self.store.check_invariants(), [])

    def assertRecovered(self):
        a = self.artifact("bad.py")
        self.assertNotEqual(a["parse_status"], "FAILED")
        self.assertEqual(a["parse_status"], "OK",
                         "the analyser's real status must be restored, not forced")
        self.assertIn(a["parse_error"], (None, ""), "a stale failure reason survived")
        g = self.graph("bad.py")
        self.assertGreater(g["symbols"], 0)
        self.assertGreater(g["claims"], 0)
        self.assertEqual(self.store.check_invariants(), [])

    def test_failure_then_success_recovers(self):
        ingest(self.store, self.root, **{self.KW: "bad.py"})
        self.assertFailedCleanly()
        ingest(self.store, self.root)
        self.assertRecovered()

    def test_identity_and_provenance_survive_the_round_trip(self):
        ingest(self.store, self.root, **{self.KW: "bad.py"})
        before = self.artifact("bad.py")
        ingest(self.store, self.root)
        after = self.artifact("bad.py")
        self.assertEqual(before["artifact_id"], after["artifact_id"],
                         "the retry created a second artifact identity")
        self.assertEqual(before["first_seen_run"], after["first_seen_run"],
                         "first_seen_run was overwritten by the retry")
        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(self.store.con.execute(
            "SELECT count(*) FROM artifact WHERE rel_path='bad.py'").fetchone()[0], 1)

    def test_a_later_failure_returns_to_failed_with_no_partial_graph(self):
        ingest(self.store, self.root, **{self.KW: "bad.py"})
        ingest(self.store, self.root)
        self.assertRecovered()
        ingest(self.store, self.root, **{self.KW: "bad.py"})
        self.assertFailedCleanly()

    def test_alternating_cycles_stay_consistent(self):
        for n in range(3):
            with self.subTest(cycle=n, phase="fail"):
                ingest(self.store, self.root, **{self.KW: "bad.py"})
                self.assertFailedCleanly()
            with self.subTest(cycle=n, phase="recover"):
                ingest(self.store, self.root)
                self.assertRecovered()

    def test_the_failure_never_leaves_another_artifact_dangling(self):
        ingest(self.store, self.root)
        self.assertEqual(self.store.check_invariants(), [])
        ingest(self.store, self.root, **{self.KW: "bad.py"})
        self.assertEqual(self.store.check_invariants(), [],
                         "an edge into the failed artifact was left pointing at a "
                         "symbol that no longer exists")
        dropped = self.store.con.execute(
            "SELECT count(*) FROM diagnostic WHERE code='INBOUND_EDGES_DROPPED'").fetchone()[0]
        self.assertGreaterEqual(dropped, 1, "inbound edge loss was silent")

    def test_the_healthy_artifact_keeps_its_own_graph(self):
        ingest(self.store, self.root, **{self.KW: "bad.py"})
        self.assertGreater(self.graph("good.py")["symbols"], 0)
        self.assertGreater(self.graph("good.py")["claims"], 0)
        self.assertEqual(self.artifact("good.py")["parse_status"], "OK")

    def test_the_report_counts_the_failure(self):
        rep = ingest(self.store, self.root, **{self.KW: "bad.py"})
        self.assertEqual(rep.extraction_failed, 1)
        self.assertEqual(rep.failed, 1)
        self.assertEqual(rep.absent, [])


    def test_recovery_restores_the_identical_graph(self):
        """A failure cycle must leave no residue.

        Purging the failed artifact removes edges INTO it, and artifacts
        resolved after the purge emit unresolved variants instead. If the
        resolve stage appended rather than replaced, those variants survived the
        retry beside the resolved edges that came back -- 156 stale duplicates
        on werkzeug, which no invariant can see because an unresolved claim is
        perfectly legal.
        """
        ingest(self.store, self.root)
        before = self.claim_rows()
        ingest(self.store, self.root, **{self.KW: "bad.py"})
        ingest(self.store, self.root)
        after = self.claim_rows()
        self.assertEqual(after - before, set(), "stale claims survived the retry")
        self.assertEqual(before - after, set(), "recovery lost claims")
        self.assertEqual(self.store.check_invariants(), [])

    def test_the_graph_does_not_grow_on_repeated_clean_ingestion(self):
        ingest(self.store, self.root)
        first = self.claim_rows()
        for _ in range(3):
            ingest(self.store, self.root)
        self.assertEqual(self.claim_rows(), first, "re-ingestion is not idempotent")


class TestStageOneRecovery(_RecoveryTests, _Corpus):
    KW = "fail_extraction_at"


class TestStageTwoRecovery(_RecoveryTests, _Corpus):
    KW = "fail_resolution_at"


class TestRepeatedCleanIngestionIsNotQuarantined(_Corpus):
    def test_a_fourth_ingestion_of_unchanged_content_still_processes(self):
        """`attempts` is keyed by content and used to quarantine. Without a
        reset on success it accumulated across a database's whole lifetime."""
        for n in range(1, 6):
            rep = ingest(self.store, self.root)
            with self.subTest(run=n):
                self.assertEqual(rep.failed, 0, "an unchanged corpus was quarantined")
                self.assertEqual(rep.absent, [])
                self.assertEqual(self.artifact("bad.py")["parse_status"], "OK")
        self.assertEqual(self.store.check_invariants(), [])


# ── decorator CALLS ───────────────────────────────────────────────────────

def calls(src: str) -> list[tuple[str, str, str]]:
    """(subject, target, cited source) for every CALLS the backend emits."""
    data = src.encode()
    an = python_backend.analyze("aid", data, "m")
    out = []
    for r in an.references:
        if r.predicate == "CALLS":
            p = r.locator.payload
            out.append((r.from_qname, r.to_name,
                        data[p["byte_start"]:p["byte_end"]].decode("utf-8")))
    return out


def decorator_diagnostics(src: str) -> list[str]:
    an = python_backend.analyze("aid", src.encode(), "m")
    return [d.message for d in an.diagnostics if d.code == "UNSUPPORTED_DECORATOR_CALL"]


class TestDecoratorCallsAreNotCalls(unittest.TestCase):
    def test_a_decorator_produces_no_calls_claim(self):
        got = calls('@app.route("/")\ndef f():\n    return 1\n')
        self.assertEqual(got, [], f"a decorator was recorded as a call: {got}")

    def test_the_same_call_in_the_body_is_still_a_call(self):
        got = calls('def f():\n    app.route("/")\n')
        self.assertEqual([(s, t) for s, t, _ in got], [("m.f", "app.route")])

    def test_a_decorated_function_keeps_its_body_calls(self):
        got = calls('@app.route("/")\ndef f():\n    app.route("/")\n    return 1\n')
        self.assertEqual(len(got), 1, f"expected exactly the body call: {got}")
        subject, target, cited = got[0]
        self.assertEqual((subject, target), ("m.f", "app.route"))
        self.assertEqual(cited, 'app.route("/")')
        # exactly one decorator was skipped, and it was the decorator
        self.assertEqual(len(decorator_diagnostics(
            '@app.route("/")\ndef f():\n    app.route("/")\n    return 1\n')), 1)

    def test_every_skipped_decorator_is_recorded(self):
        src = '@app.route("/")\n@cache(ttl=60)\ndef f():\n    return 1\n'
        msgs = decorator_diagnostics(src)
        self.assertEqual(len(msgs), 2, msgs)
        self.assertTrue(any("app.route" in m for m in msgs))
        self.assertTrue(any("cache" in m for m in msgs))
        for m in msgs:
            self.assertIn("m.f", m, "the diagnostic must name the definition")

    def test_a_bare_name_decorator_needs_no_diagnostic(self):
        """`@property` is not a Call, so it never produced a claim."""
        self.assertEqual(decorator_diagnostics("@property\ndef f():\n    return 1\n"), [])
        self.assertEqual(calls("@property\ndef f():\n    return 1\n"), [])

    def test_nested_calls_inside_a_decorator_are_also_excluded(self):
        got = calls("@deco(make_key())\ndef f():\n    return 1\n")
        self.assertEqual(got, [], f"a call nested in a decorator survived: {got}")
        self.assertEqual(len(decorator_diagnostics("@deco(make_key())\ndef f():\n    return 1\n")), 2)

    def test_class_decorators_are_excluded_too(self):
        self.assertEqual(calls("@register()\nclass C:\n    pass\n"), [])

    def test_the_mixed_fixture_keeps_exactly_the_real_calls(self):
        got = {(s, t) for s, t, _ in calls(DECORATED)}
        self.assertEqual(got, {
            ("m.decorated", "app.helper"),
            ("m.calls_the_same_thing", "app.route"),
            ("m.plain", "app.route"),
        })


class TestDecoratorFixtureIsDeterministic(unittest.TestCase):
    def test_two_ingestions_agree(self):
        snaps = []
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "corpus"
            root.mkdir()
            (root / "dec.py").write_text(DECORATED, encoding="utf-8")
            for n in range(2):
                s = Store(str(Path(td) / f"kg{n}.sqlite"))
                ingest(s, root)
                snaps.append({
                    "symbol": [tuple(r) for r in s.con.execute(
                        "SELECT symbol_id, qualified_name FROM symbol ORDER BY symbol_id")],
                    "claim": [tuple(r) for r in s.con.execute(
                        "SELECT claim_id, predicate, subject_id, object_literal FROM claim"
                        " ORDER BY claim_id")],
                    "evidence": [tuple(r) for r in s.con.execute(
                        "SELECT evidence_id, quoted_text FROM evidence ORDER BY evidence_id")],
                    "diagnostic": [tuple(r) for r in s.con.execute(
                        "SELECT diagnostic_id, code, message, line FROM diagnostic"
                        " ORDER BY diagnostic_id")],
                })
                self.assertEqual(s.check_invariants(), [])
                s.close()
        for table in snaps[0]:
            with self.subTest(table=table):
                self.assertEqual(snaps[0][table], snaps[1][table])
        self.assertTrue(any(c[1] == "CALLS" for c in snaps[0]["claim"]))
        self.assertTrue(any(d[1] == "UNSUPPORTED_DECORATOR_CALL"
                            for d in snaps[0]["diagnostic"]))


if __name__ == "__main__":
    unittest.main()
