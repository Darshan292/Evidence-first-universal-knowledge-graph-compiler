"""K-1: HAS_VALUE for direct class-body literals, end to end.

The point of this predicate is not coverage. It is that the FUNCTIONAL branch of
the conflict model is exercised by claims the compiler actually produced, rather
than by rows a test inserted into the database.

Every conflict test here therefore goes source -> ingest -> decide().
"""
from __future__ import annotations

import ast
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.retrieval.claimfirst import (EXPOSE, EXPOSE_CONFLICTED,
                                              ClaimIndex, decide)
from kgc import SCHEMA_VERSION
from kgc.analysis import python_backend
from kgc.ids import claim_id, evidence_id
from kgc.pipeline import ingest
from kgc.predicates import CANONICAL, FUNCTIONAL, TRUSTED_ESTABLISHMENT, is_trusted
from kgc.store import Store

# ── the exact supported syntax, and the exact syntax that must be refused ──
SUPPORTED = {
    "TIMEOUT": ("TIMEOUT = 30", "30"),
    "NAME": ('NAME = "production"', '"production"'),
    "ENABLED": ("ENABLED = True", "True"),
    "RATIO": ("RATIO = 0.5", "0.5"),
    "MISSING": ("MISSING = None", "None"),
}
# name -> (source line, the reason the diagnostic must record)
REFUSED = {
    "SUM":      ("SUM = 10 + 20", "value is a BinOp"),
    "CALLED":   ("CALLED = get_timeout()", "value is a Call"),
    "ALIASED":  ("ALIASED = SOME_OTHER_VALUE", "value is a Name"),
    "CHAINED":  ("CHAINED = ALSO = 10", "chained assignment"),
    "TUPLE":    ("TUP_A, TUP_B = (1, 2)", "Tuple target"),
    "ANNOTATED": ("ANNOTATED: int = 7", "annotated assignment"),
    "RAW":      ('RAW = b"30"', "literal type 'bytes'"),
    "NEGATED":  ("NEGATED = -1", "value is a UnaryOp"),
}

CLASS_SRC = "class Config:\n    \"\"\"Config.\"\"\"\n" + "".join(
    f"    {line}\n" for line, _ in SUPPORTED.values()) + "".join(
    f"    {line}\n" for line, _ in REFUSED.values())

# every context that is NOT a direct class body
OUT_OF_SCOPE = '''MODULE_LEVEL = 42


class Nested:
    if MODULE_LEVEL:
        CONDITIONAL = 1

    def method(self):
        LOCAL = 99
        return LOCAL


def function():
    FUNCTION_LOCAL = 7
    return FUNCTION_LOCAL
'''


def has_values(src: str) -> dict[str, str]:
    """qualified subject -> literal, straight from the backend."""
    an = python_backend.analyze("aid", src.encode(), "m")
    return {r.from_qname: r.to_name for r in an.references if r.predicate == "HAS_VALUE"}


def diagnostics(src: str) -> list[str]:
    an = python_backend.analyze("aid", src.encode(), "m")
    return [d.message for d in an.diagnostics if d.code == "UNSUPPORTED_CLASS_LITERAL"]


class TestSupportedSyntax(unittest.TestCase):
    def test_each_supported_literal_is_emitted_with_its_source_text(self):
        got = has_values(CLASS_SRC)
        for name, (_line, literal) in SUPPORTED.items():
            with self.subTest(name=name):
                self.assertEqual(got.get(f"m.Config.{name}"), literal)

    def test_supported_set_is_exactly_the_declared_one(self):
        got = {q.rsplit(".", 1)[-1] for q in has_values(CLASS_SRC)}
        self.assertEqual(got, set(SUPPORTED),
                         "the emitted grammar drifted from the declared grammar")


class TestRefusedSyntax(unittest.TestCase):
    def test_no_refused_form_emits_a_value(self):
        got = has_values(CLASS_SRC)
        for name in REFUSED:
            with self.subTest(name=name):
                self.assertNotIn(f"m.Config.{name}", got)

    def test_every_refusal_records_why(self):
        msgs = diagnostics(CLASS_SRC)
        self.assertEqual(len(msgs), len(REFUSED),
                         f"{len(REFUSED)} refusals, {len(msgs)} diagnostics: a "
                         f"refusal was silent")
        for name, (_line, reason) in REFUSED.items():
            with self.subTest(name=name):
                self.assertTrue(any(reason in m for m in msgs),
                                f"no diagnostic explains {name}: {msgs}")

    def test_no_arithmetic_is_ever_evaluated(self):
        """`10 + 20` must not become 30 by any path."""
        self.assertNotIn("30", "".join(has_values("class C:\n    X = 10 + 20\n").values()))

    def test_nothing_outside_a_direct_class_body_is_extracted(self):
        self.assertEqual(has_values(OUT_OF_SCOPE), {},
                         "module level, a nested `if`, a method or a function "
                         "body produced a class-literal claim")


class TestCompiledClaim(unittest.TestCase):
    """Claim shape, evidence and identity, on the real ingestion path."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "corpus"
        cls.root.mkdir()
        (cls.root / "app.py").write_text(CLASS_SRC, encoding="utf-8")
        cls.store = Store(str(Path(cls.tmp.name) / "kg.sqlite"))
        cls.report = ingest(cls.store, cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.tmp.cleanup()

    def rows(self):
        return [dict(r) for r in self.store.con.execute(
            "SELECT cl.*, s.qualified_name subj, a.rel_path, a.sha256, a.artifact_id,"
            "       e.evidence_id, e.quoted_text, e.locator, e.verification_strength"
            "  FROM claim cl"
            "  JOIN symbol s ON s.symbol_id = cl.subject_id"
            "  JOIN claim_evidence ce ON ce.claim_id = cl.claim_id"
            "  JOIN evidence e ON e.evidence_id = ce.evidence_id"
            "  JOIN artifact a ON a.artifact_id = e.artifact_id"
            " WHERE cl.predicate='HAS_VALUE' ORDER BY s.qualified_name")]

    def test_one_claim_per_supported_literal(self):
        self.assertEqual(len(self.rows()), len(SUPPORTED))

    def test_claim_shape(self):
        for r in self.rows():
            with self.subTest(subj=r["subj"]):
                self.assertEqual(r["establishment"], "DERIVED")
                self.assertEqual(r["lifecycle"], "ACTIVE")
                self.assertIsNone(r["object_id"], "a literal is not a symbol reference")
                self.assertIsNone(r["model_id"])
                self.assertIsNone(r["confidence"], "a parser result is not a probability")
                self.assertTrue(r["object_literal"])

    def test_evidence_is_the_assignment_byte_exact(self):
        data = (self.root / "app.py").read_bytes()
        for r in self.rows():
            with self.subTest(subj=r["subj"]):
                loc = json.loads(r["locator"])
                actual = data[loc["byte_start"]:loc["byte_end"]].decode("utf-8")
                self.assertEqual(actual, r["quoted_text"])
                name = r["subj"].rsplit(".", 1)[-1]
                self.assertEqual(actual, SUPPORTED[name][0],
                                 "evidence must span the assignment itself")
                self.assertEqual(r["verification_strength"], "EXACT")
                # the cited span re-parses as the assignment it claims to be
                self.assertIsInstance(ast.parse(actual).body[0], ast.Assign)

    def test_evidence_verification_rate_is_total(self):
        n = self.store.con.execute(
            "SELECT count(*) n FROM claim cl JOIN claim_evidence ce USING(claim_id)"
            " JOIN evidence e USING(evidence_id)"
            " WHERE cl.predicate='HAS_VALUE'"
            "   AND e.verification_strength NOT IN ('EXACT','REPRODUCIBLE')").fetchone()["n"]
        self.assertEqual(n, 0)

    def test_claim_and_evidence_ids_are_reproducible(self):
        data = (self.root / "app.py").read_bytes()
        for r in self.rows():
            with self.subTest(subj=r["subj"]):
                loc = json.loads(r["locator"])
                self.assertEqual(evidence_id(r["artifact_id"], r["sha256"],
                                             "byte_range", loc, r["quoted_text"]),
                                 r["evidence_id"])
                self.assertEqual(claim_id(
                    predicate="HAS_VALUE", subject_id=r["subject_id"], object_id=None,
                    object_literal=r["object_literal"], extractor_id=r["extractor_id"],
                    extractor_version=r["extractor_version"], model_id=None,
                    prompt_version=None, schema_version=SCHEMA_VERSION,
                    evidence_ids=[r["evidence_id"]]), r["claim_id"])
                self.assertTrue(len(r["claim_id"]) == 32 and int(r["claim_id"], 16) >= 0)

    def test_no_reference_detail_row(self):
        """A literal has no target symbol, so there is no resolution to record."""
        n = self.store.con.execute(
            "SELECT count(*) n FROM reference r JOIN claim c USING(claim_id)"
            " WHERE c.predicate='HAS_VALUE'").fetchone()["n"]
        self.assertEqual(n, 0)

    def test_database_invariants_hold(self):
        self.assertEqual(self.store.check_invariants(), [])

    def test_a_model_may_not_establish_a_compiled_value(self):
        """HAS_VALUE is a parser-level structural fact, so the structural
        trigger must cover it. The hand-typed trigger list did not."""
        art = self.store.con.execute(
            "SELECT artifact_id, sha256 FROM artifact WHERE sha256!='' LIMIT 1").fetchone()
        run = self.store.con.execute("SELECT run_id FROM processing_run LIMIT 1").fetchone()[0]
        subj = self.store.con.execute("SELECT symbol_id FROM symbol LIMIT 1").fetchone()[0]
        self.store.begin()
        self.store.con.execute(
            "INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?,?)",
            ("ev-proposed", art["artifact_id"], art["sha256"], "byte_range",
             '{"byte_start":0,"byte_end":1}', "x", "EXACT", "t", "now"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.con.execute(
                "INSERT INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("c-proposed", "HAS_VALUE", subj, "symbol", None, None, "30",
                 "ACTIVE", "PROPOSED", 0.9, "x", "1", "m", None,
                 SCHEMA_VERSION, run, json.dumps(["ev-proposed"]), None))
        self.store.rollback()

    def test_a_value_claim_without_a_value_is_rejected_by_the_database(self):
        art = self.store.con.execute(
            "SELECT artifact_id, sha256 FROM artifact WHERE sha256!='' LIMIT 1").fetchone()
        run = self.store.con.execute("SELECT run_id FROM processing_run LIMIT 1").fetchone()[0]
        subj = self.store.con.execute("SELECT symbol_id FROM symbol LIMIT 1").fetchone()[0]
        self.store.begin()
        self.store.con.execute(
            "INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?,?)",
            ("ev-empty", art["artifact_id"], art["sha256"], "byte_range",
             '{"byte_start":0,"byte_end":1}', "x", "EXACT", "t", "now"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.con.execute(
                "INSERT INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("c-empty", "HAS_VALUE", subj, "symbol", None, None, None,
                 "ACTIVE", "DERIVED", None, "x", "1", None, None,
                 SCHEMA_VERSION, run, json.dumps(["ev-empty"]), None))
        self.store.rollback()


class _Corpus(unittest.TestCase):
    """Ingest a source map, then ask decide() about it."""
    FILES: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        for rel, body in cls.FILES.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        cls.store = Store(str(Path(cls.tmp.name) / "kg.sqlite"))
        ingest(cls.store, root)
        cls.index = ClaimIndex(cls.store)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.tmp.cleanup()


class TestFunctionalConflictEndToEnd(_Corpus):
    """§6: conflict demonstrated on compiler output, not inserted rows.

    Two claims share a subject only when the same class body assigns the same
    name twice -- the sole way a functional contradiction can arise from
    compilation today. See K1_REPORT.md §8.
    """
    FILES = {
        "differing.py": "class Server:\n    PORT = 30\n    PORT = 60\n",
        "equivalent.py": 'class Cache:\n    TTL = 30\n    TTL = "30 seconds"\n',
        "single.py": "class Solo:\n    LIMIT = 5\n",
    }

    def test_different_values_contradict(self):
        d = decide(self.index, "Server's PORT")
        self.assertEqual(d.outcome, EXPOSE_CONFLICTED, d.reason)
        self.assertEqual(d.conflict["state"], "CONTRADICTS")
        self.assertEqual(d.conflict["predicate"], "HAS_VALUE")
        self.assertEqual(sorted(d.conflict["values"]), ["30", "60"])

    def test_equivalent_values_are_consistent(self):
        d = decide(self.index, "Cache's TTL")
        self.assertEqual(d.outcome, EXPOSE, d.reason)
        self.assertEqual(d.conflict["state"], "CONSISTENT",
                         "30 and '30 seconds' are the same value")

    def test_a_single_value_is_never_a_conflict(self):
        d = decide(self.index, "Solo's LIMIT")
        self.assertEqual(d.outcome, EXPOSE, d.reason)
        self.assertEqual([h.object_literal for h in d.hits
                          if h.predicate == "HAS_VALUE"], ["5"])

    def test_the_conflicting_claims_came_from_the_compiler(self):
        rows = self.store.con.execute(
            "SELECT extractor_id, establishment FROM claim WHERE predicate='HAS_VALUE'"
        ).fetchall()
        self.assertTrue(rows)
        self.assertEqual({r["extractor_id"] for r in rows}, {"python_ast"})
        self.assertEqual({r["establishment"] for r in rows}, {"DERIVED"})


class TestMultiValuedStillNeverConflicts(_Corpus):
    """Gate 2.5's regression, re-run beside a live functional predicate."""
    FILES = {
        "__init__.py": "",
        "alpha.py": "def helper_a():\n    return 1\n",
        "beta.py": "def helper_b():\n    return 2\n",
        "svc.py": '''from alpha import helper_a
from beta import helper_b


class BaseOne:
    pass


class BaseTwo:
    pass


class Service(BaseOne, BaseTwo):
    LIMIT = 5

    def run(self):
        helper_a()
        helper_b()
''',
    }

    def test_two_call_targets_do_not_conflict(self):
        d = decide(self.index, "what does Service.run call")
        self.assertGreaterEqual(
            len({h.object_qname or h.object_literal for h in d.hits}), 2)
        self.assertEqual(d.outcome, EXPOSE, f"two CALLS became a dispute: {d.conflict}")

    def test_multiple_inheritance_does_not_conflict(self):
        d = decide(self.index, "what does Service inherit from")
        self.assertNotEqual(d.outcome, EXPOSE_CONFLICTED,
                            f"multiple inheritance became a dispute: {d.conflict}")

    def test_a_functional_predicate_is_present_in_the_same_corpus(self):
        """Guards against the regression passing because nothing was compiled."""
        n = self.store.con.execute(
            "SELECT count(*) n FROM claim WHERE predicate='HAS_VALUE'").fetchone()["n"]
        self.assertEqual(n, 1)


class TestDeterminism(unittest.TestCase):
    def test_two_runs_produce_identical_state(self):
        """Same corpus, same configuration, two independent databases."""
        src = CLASS_SRC + "\n\n" + OUT_OF_SCOPE
        snaps = []
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "corpus"
            root.mkdir()
            (root / "app.py").write_text(src, encoding="utf-8")
            for n in range(2):
                s = Store(str(Path(td) / f"kg{n}.sqlite"))
                ingest(s, root)
                snaps.append({
                    "artifact": [tuple(r) for r in s.con.execute(
                        "SELECT artifact_id, rel_path, sha256 FROM artifact ORDER BY artifact_id")],
                    "symbol": [tuple(r) for r in s.con.execute(
                        "SELECT symbol_id, qualified_name FROM symbol ORDER BY symbol_id")],
                    "claim": [tuple(r) for r in s.con.execute(
                        "SELECT claim_id, predicate, subject_id, object_literal FROM claim"
                        " ORDER BY claim_id")],
                    "evidence": [tuple(r) for r in s.con.execute(
                        "SELECT evidence_id, quoted_text FROM evidence ORDER BY evidence_id")],
                    "diagnostic": [tuple(r) for r in s.con.execute(
                        "SELECT diagnostic_id, code, message FROM diagnostic"
                        " ORDER BY diagnostic_id")],
                })
                s.close()
        for table in snaps[0]:
            with self.subTest(table=table):
                self.assertEqual(snaps[0][table], snaps[1][table])
        self.assertTrue(any(c[1] == "HAS_VALUE" for c in snaps[0]["claim"]))


class TestTrustIsSingleSourced(_Corpus):
    """§8: one authority for establishment trust, and it is the predicate spec."""
    FILES = {"app.py": "class Config:\n    TIMEOUT = 30\n"}

    def test_support_layer_declares_no_establishment_set_of_its_own(self):
        src = Path(__file__).resolve().parents[1].joinpath(
            "experiments/retrieval/claimfirst.py").read_text()
        for level in ("DERIVED", "CONFIRMED", "PROPOSED", "DISPUTED"):
            self.assertNotIn(f'"{level}"', src,
                             f"{level} is hard-coded in the support layer again")

    def test_changing_the_predicate_definition_changes_the_decision(self):
        """Edit the spec, and only the spec; the support layer follows."""
        before = decide(self.index, "Config's TIMEOUT")
        self.assertEqual(before.outcome, EXPOSE, before.reason)
        self.assertIn("HAS_VALUE", {h.predicate for h in before.hits})

        spec = CANONICAL["HAS_VALUE"]
        # the ONLY edit: the predicate no longer permits DERIVED
        CANONICAL["HAS_VALUE"] = type(spec)(
            spec.name, spec.meaning, spec.subject_type, spec.object_type,
            spec.structural, spec.deterministic, ("CONFIRMED",),
            spec.cardinality, spec.emitted_by_compiler)
        try:
            after = decide(self.index, "Config's TIMEOUT")
        finally:
            CANONICAL["HAS_VALUE"] = spec

        self.assertNotIn("HAS_VALUE", {h.predicate for h in after.hits},
                         "the support layer ignored the predicate definition")
        self.assertIn("HAS_VALUE", {h.predicate for h in
                                    decide(self.index, "Config's TIMEOUT").hits},
                      "the spec was not restored")

    def test_a_query_answered_only_by_the_distrusted_claim_abstains(self):
        """With nothing else to fall back on, the abstention is visible."""
        spec = CANONICAL["HAS_VALUE"]
        CANONICAL["HAS_VALUE"] = type(spec)(
            spec.name, spec.meaning, spec.subject_type, spec.object_type,
            spec.structural, spec.deterministic, ("CONFIRMED",),
            spec.cardinality, spec.emitted_by_compiler)
        try:
            sym = self.store.con.execute(
                "SELECT symbol_id FROM symbol WHERE name='TIMEOUT'").fetchone()["symbol_id"]
            hits = self.index.claims_from(sym, "HAS_VALUE")
            self.assertTrue(hits)
            self.assertEqual([h for h in hits
                              if is_trusted(h.predicate, h.establishment)], [])
        finally:
            CANONICAL["HAS_VALUE"] = spec

    def test_storable_is_not_the_same_question_as_answerable(self):
        """HAS_PURPOSE may be STORED as PROPOSED and still never be trusted.

        Collapsing the two rules into one would look like a simplification and
        would silently let a model proposal become a trusted answer.
        """
        self.assertIn("PROPOSED", CANONICAL["HAS_PURPOSE"].allowed_establishment)
        self.assertFalse(is_trusted("HAS_PURPOSE", "PROPOSED"))
        self.assertNotIn("PROPOSED", TRUSTED_ESTABLISHMENT)

    def test_an_unknown_predicate_keeps_the_base_rule(self):
        self.assertTrue(is_trusted("NOT_IN_THE_VOCABULARY", "DERIVED"))
        self.assertFalse(is_trusted("NOT_IN_THE_VOCABULARY", "PROPOSED"))


class TestSpecAgreesWithReality(unittest.TestCase):
    def test_has_value_is_declared_functional_and_emitted(self):
        spec = CANONICAL["HAS_VALUE"]
        self.assertEqual(spec.cardinality, FUNCTIONAL)
        self.assertTrue(spec.may_contradict)
        self.assertTrue(spec.emitted_by_compiler)

    def test_no_predicate_claims_to_be_emitted_without_a_producer(self):
        """`emitted_by_compiler` is a fact about the code, not an aspiration."""
        produced = set()
        for src in ("class C:\n    X = 1\n\n    def m(self):\n        f()\n",
                    "import os\n\n\nclass D(C):\n    pass\n"):
            produced |= {r.predicate for r in
                         python_backend.analyze("a", src.encode(), "m").references}
        produced |= {"CONTAINS"}          # emitted by the mapper, not the backend
        declared = {p for p, s in CANONICAL.items() if s.emitted_by_compiler}
        self.assertEqual(declared - produced, set(),
                         "the vocabulary claims a predicate the compiler never emits")


if __name__ == "__main__":
    unittest.main()
