"""Gate 2.25: the trust boundary must be enforced in code, not only described.

Every test here corresponds to a gap the Gate 2 report claimed was closed while
the implementation left it open.
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
from experiments.retrieval.constraints import ParseStatus, extract
from kgc import SCHEMA_VERSION
from kgc.claim_value import DIFFERENT, SAME, UNRESOLVED, compare
from kgc.pipeline import ingest
from kgc.store import Store

FIXTURE = {
    "pkg/__init__.py": "",
    # direct vs nested vs unrelated-descendant properties
    "pkg/direct.py": '''"""Direct and nested properties."""


class Holder:
    """Has a DIRECT timeout."""

    timeout = 30

    class Nested:
        """A nested class with its OWN timeout -- not Holder's."""

        timeout = 99


class Unrelated:
    """Has a descendant whose name matches but is not a property of Holder."""

    def method(self):
        timeout = 7
        return timeout
''',
    # same symbol name in two modules -> genuine ambiguity
    "pkg/alpha.py": '''class Widget:
    """Widget in alpha."""

    size = 10
''',
    "pkg/beta.py": '''class Widget:
    """Widget in beta."""

    size = 20
''',
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
        cls.db = str(Path(cls.tmp.name) / "kg.sqlite")
        cls.store = Store(cls.db)
        ingest(cls.store, root)
        cls.index = ClaimIndex(cls.store)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.tmp.cleanup()

    def insert_claim(self, cid, establishment, lifecycle="ACTIVE", predicate="HAS_PURPOSE",
                     model_id="llama3.1:8b", strength="EXACT"):
        """Insert a claim at a chosen establishment, bypassing the pipeline."""
        run = self.store.con.execute("SELECT run_id FROM processing_run LIMIT 1").fetchone()[0]
        art = self.store.con.execute(
            "SELECT artifact_id, sha256 FROM artifact WHERE sha256!='' LIMIT 1").fetchone()
        subj = self.store.con.execute(
            "SELECT symbol_id FROM symbol WHERE name='Holder'").fetchone()[0]
        eid = f"ev-{cid}"
        self.store.begin()
        self.store.con.execute(
            "INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?,?)",
            (eid, art["artifact_id"], art["sha256"], "byte_range",
             '{"byte_start":0,"byte_end":5}', "class", strength, "t", "now"))
        self.store.con.execute(
            "INSERT OR IGNORE INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, predicate, subj, "symbol", None, None, "some purpose",
             lifecycle, establishment, 0.97, "x", "1",
             None if establishment == "DERIVED" else model_id, None,
             SCHEMA_VERSION, run, json.dumps([eid]), None))
        self.store.con.execute("INSERT OR IGNORE INTO claim_evidence VALUES(?,?)", (cid, eid))
        self.store.commit()
        return cid


class TestEstablishmentBoundary(Base):
    """Item 1: PROPOSED/DISPUTED must never become an ordinary EXPOSE."""

    def test_proposed_claim_cannot_produce_ordinary_expose(self):
        self.insert_claim("c-proposed", "PROPOSED")
        d = decide(self.index, "what is the purpose of Holder")
        self.assertNotEqual(d.outcome, EXPOSE,
                            "a model-proposed claim became a trusted answer")
        self.assertIn(d.outcome, (ABSTAIN, ABSTAIN_AMBIGUOUS))

    def test_disputed_claim_cannot_produce_ordinary_expose(self):
        self.insert_claim("c-disputed", "DISPUTED")
        d = decide(self.index, "what is the purpose of Holder")
        self.assertNotEqual(d.outcome, EXPOSE)

    def test_derived_claim_with_verified_evidence_can_expose(self):
        d = decide(self.index, "what is the timeout of Holder")
        self.assertEqual(d.outcome, EXPOSE)
        self.assertTrue(all(h.establishment in ("DERIVED", "CONFIRMED") for h in d.hits))

    def test_proposed_information_remains_queryable(self):
        self.insert_claim("c-visible", "PROPOSED")
        rows = self.store.con.execute(
            "SELECT establishment FROM claim WHERE claim_id='c-visible'").fetchall()
        self.assertEqual(len(rows), 1, "proposed claim was filtered out of the database")
        self.assertEqual(rows[0]["establishment"], "PROPOSED")

    def test_superseded_information_remains_queryable(self):
        self.insert_claim("c-superseded", "DERIVED", lifecycle="SUPERSEDED")
        rows = self.store.con.execute(
            "SELECT lifecycle FROM claim WHERE claim_id='c-superseded'").fetchall()
        self.assertEqual(len(rows), 1, "superseded claim disappeared from the store")

    def test_support_layer_never_reads_retrieval_signals(self):
        """Checked on the AST, not on raw text: the module docstring legitimately
        names the signals it refuses to use."""
        import ast
        path = (Path(__file__).resolve().parents[1]
                / "experiments/retrieval/claimfirst.py")
        tree = ast.parse(path.read_text())
        banned = {"bm25", "rank", "score", "lexical", "confidence", "r1", "r2",
                  "overlap", "idf"}
        used = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                used.add(n.id.lower())
            elif isinstance(n, ast.Attribute):
                used.add(n.attr.lower())
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                mod = getattr(n, "module", "") or ""
                used.add(mod.lower())
                for a in n.names:
                    used.add(a.name.lower())
        leaked = sorted(b for b in banned if b in used)
        self.assertEqual(leaked, [],
                         f"support layer reads retrieval signal(s): {leaked}")

        # and it must not import the retriever at all
        imports = {getattr(n, "module", "") or "" for n in ast.walk(tree)
                   if isinstance(n, ast.ImportFrom)}
        self.assertFalse(any("retrievers" in m for m in imports),
                         "support layer imports the retriever")


class TestPropertyTraversal(Base):
    """Item 2: a nested descendant must not answer for a direct property."""

    def test_direct_property_resolves(self):
        d = decide(self.index, "what is the timeout of Holder")
        self.assertEqual(d.outcome, EXPOSE)
        self.assertTrue(any("Holder.timeout" in h.subject_qname or
                            "Holder.timeout" in (h.object_qname or "") for h in d.hits),
                        f"direct property not used; hits={[h.subject_qname for h in d.hits]}")

    def test_nested_class_property_is_not_the_parents(self):
        holder = self.index.find_symbols("Holder")
        self.assertTrue(holder)
        kids = {c["name"] for c in self.index.direct_children(holder[0]["symbol_id"])}
        self.assertIn("timeout", kids)
        self.assertIn("Nested", kids)
        nested_kids = [c for c in self.index.direct_children(holder[0]["symbol_id"])
                       if c["name"] == "Nested"]
        deep = {c["name"] for c in self.index.direct_children(nested_kids[0]["symbol_id"])}
        self.assertIn("timeout", deep)
        # the nested timeout is NOT a direct child of Holder
        direct_qnames = {c["qualified_name"] for c in
                         self.index.direct_children(holder[0]["symbol_id"])}
        self.assertNotIn("pkg.direct.Holder.Nested.timeout", direct_qnames)

    def test_unrelated_descendant_with_matching_name_does_not_answer(self):
        d = decide(self.index, "what is the timeout of Unrelated")
        self.assertIn(d.outcome, (ABSTAIN, ABSTAIN_AMBIGUOUS),
                      "a local variable inside a method answered as a class property")
        self.assertEqual(d.failed_invariant, "INV-2_predicate")

    def test_direct_children_uses_parent_identity_not_prefix(self):
        src = (Path(__file__).resolve().parents[1]
               / "experiments/retrieval/claimfirst.py").read_text()
        self.assertIn("s.parent_id = ?", src)
        self.assertNotIn('LIKE ?", (f"{qualified_name}.%"', src)


class TestAmbiguity(Base):
    """Item 3: ambiguity abstains; candidate ordering never decides identity."""

    def test_identical_names_in_separate_modules_abstain(self):
        d = decide(self.index, "what is the size of Widget")
        self.assertEqual(d.outcome, ABSTAIN_AMBIGUOUS,
                         "arbitrarily selected one of two same-named classes")
        self.assertEqual(d.failed_invariant, "INV-10_ambiguity")

    def test_scope_qualifier_resolves_ambiguity(self):
        d = decide(self.index, "what is the size of Widget in alpha.py")
        self.assertEqual(d.outcome, EXPOSE)
        self.assertTrue(all(h.rel_path.endswith("alpha.py") for h in d.hits))

    def test_no_truncation_of_candidate_symbols(self):
        src = (Path(__file__).resolve().parents[1]
               / "experiments/retrieval/claimfirst.py").read_text()
        self.assertNotIn("find_symbols(ent)[:4]", src)
        self.assertNotIn("[:4]", src, "candidate list is still truncated arbitrarily")

    def test_multi_entity_query_is_ambiguous_not_first_wins(self):
        c = extract("Widget Holder")
        self.assertEqual(c.parse_status, ParseStatus.AMBIGUOUS)


class TestQueryIRContract(Base):
    """Item 7: a partial parse must never become a different question."""

    def test_port_question_never_degrades_to_definition(self):
        c = extract("what port does TimestampSigner listen on")
        self.assertEqual(c.parse_status, ParseStatus.PARTIAL)
        self.assertNotEqual(c.shape, "identifier")
        d = decide(self.index, "what port does Holder listen on")
        self.assertEqual(d.outcome, ABSTAIN)
        self.assertEqual(d.failed_invariant, "INV-1_constraints")

    def test_prose_question_is_unparseable(self):
        self.assertEqual(extract("why should different salts be used").parse_status,
                         ParseStatus.UNPARSEABLE)

    def test_bare_identifier_parses(self):
        self.assertEqual(extract("SignatureExpired").parse_status, ParseStatus.PARSED)


class TestConflictSemantics(Base):
    """Item 4: conflict is decided on claim value, not evidence wording."""

    def test_same_value_different_wording_is_not_a_conflict(self):
        self.assertEqual(compare("timeout = 30 seconds",
                                 "gateway timeout defaults to 30 s"), SAME)

    def test_different_value_is_a_conflict(self):
        self.assertEqual(compare("timeout = 30 seconds", "timeout = 60 seconds"), DIFFERENT)

    def test_unit_conversion_is_handled(self):
        self.assertEqual(compare("0.5 min", "30 s"), SAME)

    def test_outside_the_closed_domain_is_unresolved_not_guessed(self):
        self.assertEqual(compare("a prose sentence about signing",
                                 "an unrelated prose sentence"), UNRESOLVED)
        self.assertEqual(compare("30 frobnitzes", "30 seconds"), UNRESOLVED)

    def test_conflict_not_computed_from_raw_evidence_text(self):
        src = (Path(__file__).resolve().parents[1]
               / "experiments/retrieval/claimfirst.py").read_text()
        self.assertIn("from kgc.claim_value import", src)
        self.assertNotIn("{h.evidence_text.strip() for h in", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
