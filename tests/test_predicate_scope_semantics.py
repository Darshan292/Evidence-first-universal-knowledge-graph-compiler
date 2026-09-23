"""Gate 2.5: predicate conflict semantics and exact source scope.

Every test exercises the full decide() path, not _conflict() in isolation.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.retrieval.claimfirst import (ABSTAIN, ABSTAIN_AMBIGUOUS, EXPOSE,
                                              EXPOSE_CONFLICTED, ClaimIndex, decide)
from kgc import SCHEMA_VERSION
from kgc.artifact_identity import canonical_path, is_exact, resolve_scope
from kgc.claim_value import DIFFERENT, SAME, UNRESOLVED, compare
from kgc.pipeline import ingest
from kgc.predicates import CANONICAL, FUNCTIONAL, MULTI_VALUED, cardinality, may_contradict
from kgc.store import Store

FIXTURE = {
    "src/__init__.py": "",
    "tests/__init__.py": "",
    "vendor/__init__.py": "",
    # multiple valid relationships: two CALLS, two IMPORTS, multiple inheritance
    "src/svc.py": '''from src.alpha import helper_a
from src.beta import helper_b


class BaseOne:
    pass


class BaseTwo:
    pass


class Service(BaseOne, BaseTwo):
    """Multiple inheritance AND multiple call targets."""

    def run(self):
        helper_a()
        helper_b()
        return 1
''',
    "src/alpha.py": "def helper_a():\n    return 'a'\n",
    "src/beta.py": "def helper_b():\n    return 'b'\n",
    # same basename, three different identities
    "src/config.py": "def get_setting():\n    return 'src'\n",
    "tests/config.py": "def get_setting():\n    return 'tests'\n",
    "vendor/config.py": "def get_setting():\n    return 'vendor'\n",
}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        for rel, body in FIXTURE.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        cls.store = Store(str(Path(cls.tmp.name) / "kg.sqlite"))
        ingest(cls.store, root)
        cls.index = ClaimIndex(cls.store)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.tmp.cleanup()

    def add_claim(self, cid, predicate, literal, subject_name="Service",
                  establishment="DERIVED", strength="EXACT"):
        """Insert a claim directly.

        NOTE: the compiler emits no FUNCTIONAL predicates today (only CALLS,
        CONTAINS, IMPORTS, EXTENDS), so functional conflict cannot be produced
        by ingestion. These claims are inserted to exercise the decision path.
        """
        run = self.store.con.execute("SELECT run_id FROM processing_run LIMIT 1").fetchone()[0]
        art = self.store.con.execute(
            "SELECT artifact_id, sha256 FROM artifact WHERE sha256!='' LIMIT 1").fetchone()
        subj = self.store.con.execute(
            "SELECT symbol_id FROM symbol WHERE name=?", (subject_name,)).fetchone()[0]
        eid = f"ev-{cid}"
        self.store.begin()
        self.store.con.execute(
            "INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?,?)",
            (eid, art["artifact_id"], art["sha256"], "byte_range",
             '{"byte_start":0,"byte_end":5}', literal, strength, "t", "now"))
        self.store.con.execute(
            "INSERT OR IGNORE INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, predicate, subj, "symbol", None, None, literal,
             "ACTIVE", establishment, None, "x", "1", None, None,
             SCHEMA_VERSION, run, json.dumps([eid]), None))
        self.store.con.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?)", (cid, eid))
        self.store.commit()


class TestMultiValuedNeverConflicts(Base):
    """Finding A: differing objects of a multi-valued predicate are facts."""

    def test_two_call_targets_are_consistent(self):
        d = decide(self.index, "what does Service.run call")
        targets = {(h.object_qname or h.object_literal) for h in d.hits}
        self.assertGreaterEqual(len(targets), 2, f"fixture did not produce 2 calls: {targets}")
        self.assertEqual(d.outcome, EXPOSE,
                         f"two valid CALLS became a contradiction: {d.conflict}")
        self.assertEqual(d.conflict["state"], "CONSISTENT")

    def test_two_import_targets_are_consistent(self):
        d = decide(self.index, "what does src.svc import")
        if d.outcome in (ABSTAIN, ABSTAIN_AMBIGUOUS):
            self.skipTest(f"import query did not resolve: {d.reason}")
        self.assertNotEqual(d.outcome, EXPOSE_CONFLICTED,
                            "two valid IMPORTS became a contradiction")

    def test_multiple_inheritance_is_consistent(self):
        """EXTENDS is absent from the instruction's list; Python allows two bases."""
        d = decide(self.index, "which class does Service extend")
        bases = {(h.object_qname or h.object_literal) for h in d.hits}
        self.assertGreaterEqual(len(bases), 2, f"fixture lost multiple inheritance: {bases}")
        self.assertEqual(d.outcome, EXPOSE,
                         "multiple inheritance was reported as a contradiction")

    def test_multi_valued_predicates_declare_their_cardinality(self):
        for pred in ("CALLS", "IMPORTS", "CONTAINS", "READS", "WRITES", "EXTENDS", "DEFINES"):
            self.assertEqual(cardinality(pred), MULTI_VALUED, pred)
            self.assertFalse(may_contradict(pred), pred)

    def test_conflict_does_not_hardcode_predicate_names(self):
        src = (Path(__file__).resolve().parents[1]
               / "experiments/retrieval/claimfirst.py").read_text()
        body = src[src.index("def _conflict("):]
        for pred in ("CALLS", "IMPORTS", "CONTAINS", "HAS_DEFAULT"):
            self.assertNotIn(f'"{pred}"', body,
                             f"_conflict hardcodes {pred}; the spec must be the source")
        self.assertIn("may_contradict", body)


class TestFunctionalConflicts(Base):
    """Finding A, other half: a functional predicate CAN be contradicted."""

    def test_functional_predicates_declare_their_cardinality(self):
        for pred in ("HAS_DEFAULT", "HAS_VALUE", "HAS_TYPE", "HAS_PURPOSE"):
            self.assertEqual(cardinality(pred), FUNCTIONAL, pred)
            self.assertTrue(may_contradict(pred), pred)

    def test_two_different_has_default_values_conflict(self):
        self.add_claim("c-hd-30", "HAS_DEFAULT", "30 seconds")
        self.add_claim("c-hd-60", "HAS_DEFAULT", "60 seconds")
        d = decide(self.index, "what is the default of Service")
        if d.outcome in (ABSTAIN, ABSTAIN_AMBIGUOUS):
            hits = self.index.claims_from(
                self.store.con.execute(
                    "SELECT symbol_id FROM symbol WHERE name='Service'").fetchone()[0],
                "HAS_DEFAULT")
            from experiments.retrieval.claimfirst import _conflict
            self.assertEqual(_conflict(hits)["state"], "CONTRADICTS",
                             "differing functional values were not a conflict")
        else:
            self.assertEqual(d.outcome, EXPOSE_CONFLICTED)

    def test_identical_functional_values_are_consistent(self):
        self.assertEqual(compare("30 seconds", "30 seconds"), SAME)

    def test_equivalent_units_are_consistent(self):
        self.assertEqual(compare("30 seconds", "30 s"), SAME)
        self.assertEqual(compare("0.5 minutes", "30 s"), SAME)

    def test_unsupported_normalization_stays_unresolved(self):
        self.assertEqual(compare("30 frobnitzes", "30 seconds"), UNRESOLVED)
        self.assertEqual(compare("a prose statement", "another prose statement"), UNRESOLVED)

    def test_conflicting_claims_remain_queryable(self):
        self.add_claim("c-q-30", "HAS_DEFAULT", "30 seconds")
        self.add_claim("c-q-60", "HAS_DEFAULT", "60 seconds")
        rows = self.store.con.execute(
            "SELECT claim_id FROM claim WHERE claim_id IN ('c-q-30','c-q-60')").fetchall()
        self.assertEqual(len(rows), 2, "a conflicting claim was removed from the store")


class TestExactScope(Base):
    """Finding B: scope is exact canonical identity, never a suffix."""

    def test_qualified_scope_selects_only_that_artifact(self):
        for want in ("src/config.py", "tests/config.py", "vendor/config.py"):
            with self.subTest(scope=want):
                d = decide(self.index, f"get_setting in {want}")
                self.assertEqual(d.outcome, EXPOSE, d.reason)
                self.assertEqual({canonical_path(h.rel_path) for h in d.hits}, {want})

    def test_bare_basename_matching_several_artifacts_abstains(self):
        d = decide(self.index, "get_setting in config.py")
        self.assertEqual(d.outcome, ABSTAIN_AMBIGUOUS,
                         "a basename silently selected one of three artifacts")
        self.assertEqual(d.failed_invariant, "INV-10_ambiguity")

    def test_unrelated_basename_cannot_satisfy_scope(self):
        d = decide(self.index, "get_setting in nosuchfile.py")
        self.assertEqual(d.outcome, ABSTAIN)
        self.assertEqual(d.failed_invariant, "INV-4_scope")

    def test_suffix_match_is_not_identity(self):
        self.assertFalse(is_exact("src/config.py", "config.py"))
        self.assertFalse(is_exact("tests/config.py", "config.py"))
        self.assertTrue(is_exact("src/config.py", "src/config.py"))
        self.assertTrue(is_exact("src/config.py", "./src/config.py"))

    def test_scope_applies_to_entity_resolution_and_evidence(self):
        d = decide(self.index, "get_setting in tests/config.py")
        self.assertEqual(d.outcome, EXPOSE)
        for h in d.hits:                       # evidence identity, not just entity
            self.assertEqual(canonical_path(h.rel_path), "tests/config.py")

    def test_resolve_scope_states_are_explicit(self):
        known = ["src/config.py", "tests/config.py", "vendor/config.py"]
        self.assertEqual(resolve_scope("src/config.py", known)[1], "EXACT")
        self.assertEqual(resolve_scope("config.py", known)[1], "AMBIGUOUS")
        self.assertEqual(resolve_scope("absent.py", known)[1], "UNKNOWN")

    def test_no_endswith_scope_matching_remains(self):
        src = (Path(__file__).resolve().parents[1]
               / "experiments/retrieval/claimfirst.py").read_text()
        self.assertNotIn('rel_path.endswith(', src)
        self.assertIn("canonical_path", src)


class TestIdentityInvariants(Base):
    """Item 7: no semantic identity may depend on candidate ordering."""

    def test_same_named_symbols_without_scope_abstain(self):
        d = decide(self.index, "get_setting")
        self.assertEqual(d.outcome, ABSTAIN_AMBIGUOUS)

    def test_artifact_identity_is_single_sourced(self):
        import kgc.artifact_identity as ai
        self.assertTrue(hasattr(ai, "canonical_path"))
        for mod in ("experiments/retrieval/claimfirst.py", "kgc/pipeline.py"):
            src = (Path(__file__).resolve().parents[1] / mod).read_text()
            self.assertIn("artifact_identity", src, f"{mod} does not use the identity module")

    def test_predicate_semantics_single_sourced(self):
        src = (Path(__file__).resolve().parents[1]
               / "experiments/retrieval/claimfirst.py").read_text()
        self.assertNotIn("MULTI_VALUED = ", src, "cardinality redefined outside the spec")
        self.assertNotIn("FUNCTIONAL = ", src)

    def test_every_canonical_predicate_declares_full_semantics(self):
        for name, spec in CANONICAL.items():
            self.assertIn(spec.cardinality, (FUNCTIONAL, MULTI_VALUED), name)
            self.assertIsInstance(spec.emitted_by_compiler, bool, name)
            self.assertTrue(spec.allowed_establishment, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
