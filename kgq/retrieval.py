"""Deterministic retrieval. No model, no embeddings, no vector store.

Measured before this was written (ARCHITECTURE_PIVOT_AFTER_J1.md §2.4): over the
42 answerable J-1 questions, exact identifier lookup reaches the gold evidence
file for 57%, and BM25 over corpus files reaches it for 90% at top-5 and 100% at
top-10. A vector database has no empirical trigger, so there is not one here.

Two layers, in this order:

  1. EXACT IDENTIFIER   a name in the question that is a compiled symbol.
                        Precise, free, and it is how a developer refers to code.
  2. GRAPH EXPANSION    one hop along RESOLVED CALLS edges from those symbols.
                        This is what having a compiler is for: the answer to
                        "how does send_from_directory prevent unsafe paths" lives
                        in safe_join, which the question never names and which no
                        lexical search would rank highly.
  3. FTS5 / BM25        over symbol spans and their docstrings -- the fallback
                        for questions that name nothing the graph knows.

Both return EXISTING evidence ids. Nothing here mints an id or a locator: the
model can only ever cite what the compiler already recorded.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
FILE_SCOPE = re.compile(
    r"(?<![\w/])((?:[A-Za-z_][\w.-]*/)*[A-Za-z_][\w.-]*\.(?:py|rst|md|toml|txt))\b")
STOP = set("""what which how when why where who does do is are can could should the a an of
for to and or in on at into from by with as if not it this that these those i my our your
their there here me us them explain tell show give help please about between difference""".split())
# A span short enough to be a single call site teaches a reader nothing about
# behaviour; the function that contains it does.
MIN_SPAN = 180


@dataclass
class EvidenceSpan:
    """One retrievable piece of source, addressed by an id the compiler minted."""
    evidence_id: str
    rel_path: str
    byte_start: int
    byte_end: int
    line_start: int | None
    text: str
    symbol: str
    kind: str
    how: str                     # which layer found it -- shown to the user
    score: float = 0.0

    def as_dict(self) -> dict:
        return {"evidence_id": self.evidence_id, "rel_path": self.rel_path,
                "byte_start": self.byte_start, "byte_end": self.byte_end,
                "line_start": self.line_start, "symbol": self.symbol,
                "kind": self.kind, "how": self.how, "chars": len(self.text)}


@dataclass
class StructuralFact:
    """A DERIVED claim, shown beside the answer and never produced by a model."""
    claim_id: str
    predicate: str
    subject: str
    object: str
    resolved: bool
    establishment: str
    evidence_id: str
    rel_path: str
    byte_start: int
    byte_end: int
    text: str

    def as_dict(self) -> dict:
        return {"claim_id": self.claim_id, "predicate": self.predicate,
                "subject": self.subject, "object": self.object,
                "resolved": self.resolved, "establishment": self.establishment,
                "evidence_id": self.evidence_id, "rel_path": self.rel_path,
                "byte_start": self.byte_start, "byte_end": self.byte_end,
                "excerpt": self.text}


def looks_like_code(token: str) -> bool:
    """Whether a word in a question is plausibly a code identifier.

    An ALL-CAPS word with no underscore or dot is an acronym in prose -- URL,
    HTTP, GET, PIN -- not a name the asker is pointing at. Treating one as a
    subject made "how does MapAdapter.build construct a URL?" resolve `URL` to
    two symbols and abstain for ambiguity, which is wrong: the asker never named
    a symbol called URL.

    The trade, stated: a question about a genuinely ALL-CAPS constant with no
    underscore (`COEP`) will not treat it as a subject. Lexical search still
    finds it; only subject identity is affected.
    """
    if token.lower() in STOP or len(token) < 3:
        return False
    if "_" in token or "." in token:
        return True
    if token.isupper():
        return False
    return any(c.isupper() for c in token)


def candidate_identifiers(question: str) -> list[str]:
    """Names in the question that could plausibly be code. Order is stable."""
    out, seen = [], set()
    for t in IDENT.findall(question):
        if looks_like_code(t) and t not in seen:
            seen.add(t); out.append(t)
    return out


class Retriever:
    def __init__(self, db_path: str, corpus_root: str):
        self.con = sqlite3.connect(db_path)
        self.con.row_factory = sqlite3.Row
        self.root = Path(corpus_root)
        # spans that matched but did not fit the budget -- reported, not hidden
        self.last_skipped: list[EvidenceSpan] = []

    def close(self) -> None:
        self.con.close()

    # ── layer 1: exact identifier ───────────────────────────────────────
    def symbols_named(self, name: str, prefer_src: bool = True) -> list[dict]:
        bare = name.split(".")[-1]
        rows = [dict(r) for r in self.con.execute(
            "SELECT s.symbol_id, s.qualified_name, s.name, s.kind, a.rel_path,"
            "       a.parse_status"
            "  FROM symbol s JOIN artifact a USING(artifact_id)"
            " WHERE s.name = ? OR s.qualified_name = ? OR s.qualified_name GLOB ?"
            " ORDER BY s.qualified_name", (bare, name, f"*.{name}"))]
        if prefer_src:
            src = [r for r in rows if r["rel_path"].startswith("src/")]
            if src:
                return src
        return rows

    def spans_for_symbol(self, symbol_id: str) -> list[EvidenceSpan]:
        """Evidence whose span is this symbol's own source, via the CONTAINS
        claim that names it. That claim's evidence is the whole definition."""
        return [self._span(r, "identifier") for r in self.con.execute(
            "SELECT e.evidence_id, e.quoted_text, e.locator, a.rel_path,"
            "       s.qualified_name, s.kind"
            "  FROM claim cl JOIN symbol s ON s.symbol_id = cl.object_id"
            "  JOIN claim_evidence ce ON ce.claim_id = cl.claim_id"
            "  JOIN evidence e ON e.evidence_id = ce.evidence_id"
            "  JOIN artifact a ON a.artifact_id = e.artifact_id"
            " WHERE cl.predicate='CONTAINS' AND cl.object_id = ?"
            " ORDER BY length(e.quoted_text) DESC LIMIT 1", (symbol_id,))]

    # ── layer 2: FTS5 / BM25 over spans ─────────────────────────────────
    def ensure_index(self) -> int:
        """Build the span index once, inside the same database. Idempotent."""
        cur = self.con.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='span_fts'").fetchone()[0]
        if cur:
            return self.con.execute("SELECT count(*) FROM span_fts").fetchone()[0]
        self.con.execute("CREATE VIRTUAL TABLE span_fts USING fts5("
                         " evidence_id UNINDEXED, body, tokenize='porter unicode61')")
        rows = self.con.execute(
            "SELECT e.evidence_id, e.quoted_text, s.qualified_name, s.docstring"
            "  FROM claim cl JOIN symbol s ON s.symbol_id = cl.object_id"
            "  JOIN claim_evidence ce ON ce.claim_id = cl.claim_id"
            "  JOIN evidence e ON e.evidence_id = ce.evidence_id"
            " WHERE cl.predicate='CONTAINS'"
            "   AND s.kind IN ('function','method','class')"
            "   AND length(e.quoted_text) >= ?", (MIN_SPAN,)).fetchall()
        seen = set()
        for r in rows:
            if r["evidence_id"] in seen:
                continue
            seen.add(r["evidence_id"])
            # the qualified name is split so `send_from_directory` also matches
            # a question that says "send from directory"
            words = re.sub(r"[._]", " ", r["qualified_name"] or "")
            self.con.execute("INSERT INTO span_fts VALUES(?,?)",
                             (r["evidence_id"], f"{words}\n{r['docstring'] or ''}\n{r['quoted_text']}"))
        self.con.commit()
        return len(seen)

    def search(self, question: str, limit: int = 5) -> list[EvidenceSpan]:
        self.ensure_index()
        terms = [t for t in re.split(r"[^A-Za-z0-9_]+", question)
                 if t and t.lower() not in STOP and len(t) > 2]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        try:
            rows = self.con.execute(
                "SELECT f.evidence_id, bm25(span_fts) AS score, e.quoted_text, e.locator,"
                "       a.rel_path, s.qualified_name, s.kind"
                "  FROM span_fts f JOIN evidence e ON e.evidence_id = f.evidence_id"
                "  JOIN artifact a ON a.artifact_id = e.artifact_id"
                "  LEFT JOIN claim cl ON cl.claim_id ="
                "       (SELECT claim_id FROM claim_evidence WHERE evidence_id=f.evidence_id LIMIT 1)"
                "  LEFT JOIN symbol s ON s.symbol_id = cl.object_id"
                " WHERE span_fts MATCH ? ORDER BY score LIMIT ?", (match, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [self._span(r, "fts5/bm25", score=r["score"]) for r in rows]

    # ── structural facts, for display beside the answer ─────────────────
    def structural_facts(self, symbol_id: str, limit: int = 40) -> list[StructuralFact]:
        out = []
        for r in self.con.execute(
            "SELECT cl.claim_id, cl.predicate, cl.object_literal, cl.establishment,"
            "       s.qualified_name AS subj, o.qualified_name AS obj,"
            "       e.evidence_id, e.quoted_text, e.locator, a.rel_path"
            "  FROM claim cl JOIN symbol s ON s.symbol_id = cl.subject_id"
            "  LEFT JOIN symbol o ON o.symbol_id = cl.object_id"
            "  JOIN claim_evidence ce ON ce.claim_id = cl.claim_id"
            "  JOIN evidence e ON e.evidence_id = ce.evidence_id"
            "  JOIN artifact a ON a.artifact_id = e.artifact_id"
            " WHERE cl.subject_id = ? AND cl.predicate != 'CONTAINS'"
            " ORDER BY cl.predicate, obj, cl.object_literal LIMIT ?", (symbol_id, limit)):
            loc = json.loads(r["locator"])
            out.append(StructuralFact(
                r["claim_id"], r["predicate"], r["subj"],
                r["obj"] or r["object_literal"] or "", r["obj"] is not None,
                r["establishment"], r["evidence_id"], r["rel_path"],
                loc["byte_start"], loc["byte_end"], r["quoted_text"]))
        return out

    def called_symbols(self, symbol_id: str, limit: int = 4) -> list[dict]:
        """Symbols this one calls, where the call RESOLVED to a real symbol.

        Unresolved targets are skipped: a name like `os.path.join` has no body
        in this corpus, and guessing one would be exactly the fabrication the
        rest of the system is built to prevent.
        """
        return [dict(r) for r in self.con.execute(
            "SELECT DISTINCT o.symbol_id, o.qualified_name, o.kind, a.rel_path"
            "  FROM claim cl JOIN symbol o ON o.symbol_id = cl.object_id"
            "  JOIN artifact a ON a.artifact_id = o.artifact_id"
            " WHERE cl.subject_id = ? AND cl.predicate = 'CALLS'"
            "   AND o.kind IN ('function','method','class')"
            " ORDER BY o.qualified_name LIMIT ?", (symbol_id, limit))]

    def claims_of(self, subject_qname: str, predicate: str) -> list[dict]:
        """Used by the structural check. Deterministic claims only."""
        return [dict(r) for r in self.con.execute(
            "SELECT cl.claim_id, cl.establishment, cl.object_literal,"
            "       o.qualified_name AS obj, a.parse_status"
            "  FROM claim cl JOIN symbol s ON s.symbol_id = cl.subject_id"
            "  JOIN artifact a ON a.artifact_id = s.artifact_id"
            "  LEFT JOIN symbol o ON o.symbol_id = cl.object_id"
            " WHERE s.qualified_name = ? AND cl.predicate = ?", (subject_qname, predicate))]

    # ── identity: deterministic, or refused ─────────────────────────────
    def resolve_identity(self, question: str, subject_hint: str | None = None) -> dict:
        """Resolve every named subject to ONE symbol, or report ambiguity.

        The model may decide which word the question is about. It may not decide
        WHICH `Response` that word means. This is the compiler's own rule --
        `decide()` abstains when a name resolves to several qualified names --
        applied before any semantic step.

        Deterministic disambiguators, and nothing else:
          * the name is fully qualified and matches exactly one symbol;
          * the question carries a file scope that resolves to one artifact,
            and exactly one candidate lives in it.

        No fuzzy ranking, no model confidence, no lexical score.
        """
        names = list(candidate_identifiers(question))
        if subject_hint and looks_like_code(subject_hint) and subject_hint not in names:
            names.insert(0, subject_hint)
        scope = self.scope_in(question)
        report = {"scope": scope, "names": [], "ambiguous": []}
        for name in names:
            rows = [s for s in self.symbols_named(name, prefer_src=False)
                    if s["kind"] in ("class", "function", "method")]
            if not rows:
                continue
            distinct = sorted({s["qualified_name"] for s in rows})
            chosen = None
            if len(distinct) == 1:
                chosen = distinct[0]
            elif name in distinct:                      # fully qualified, exact
                chosen = name
            elif scope:
                here = sorted({s["qualified_name"] for s in rows
                               if s["rel_path"] == scope})
                if len(here) == 1:
                    chosen = here[0]
            entry = {"name": name, "candidates": distinct, "resolved": chosen}
            report["names"].append(entry)
            if chosen is None:
                report["ambiguous"].append(entry)
        return report

    def scope_in(self, question: str) -> str | None:
        """A file path in the question that names exactly one artifact."""
        from kgc.artifact_identity import resolve_scope
        paths = [r["rel_path"] for r in self.con.execute(
            "SELECT DISTINCT rel_path FROM artifact")]
        for m in FILE_SCOPE.finditer(question):
            matches, status = resolve_scope(m.group(1), paths)
            if status == "EXACT":
                return matches[0]
        return None

    # ── the retrieval entry point ───────────────────────────────────────
    def retrieve(self, question: str, subject_hint: str | None = None, *,
                 max_chars: int = 24000, limit: int = 6) -> tuple[list[EvidenceSpan], list[dict]]:
        """Returns (evidence spans, resolved subject candidates).

        Identifier hits come first because they are exact. BM25 fills the rest.
        The character cap is enforced here, before anything reaches a model.
        """
        names = list(candidate_identifiers(question))
        if subject_hint and subject_hint not in names:
            names.insert(0, subject_hint)
        spans: list[EvidenceSpan] = []
        subjects: list[dict] = []
        seen: set[str] = set()
        # 1. exact identifier
        for name in names:
            syms = self.symbols_named(name)
            for s in syms:
                if s["kind"] in ("function", "method", "class"):
                    subjects.append({**s, "matched": name})
            for s in syms[:3]:
                # a value lives in a one-line assignment; the length floor is
                # there to drop lone call sites, not to hide a constant
                floor = 0 if s["kind"] in ("variable", "constant") else MIN_SPAN
                for sp in self.spans_for_symbol(s["symbol_id"]):
                    if sp.evidence_id not in seen and len(sp.text) >= floor:
                        seen.add(sp.evidence_id); spans.append(sp)

        # 2. one hop along resolved CALLS edges from the identified subjects
        for s in subjects[:2]:
            for callee in self.called_symbols(s["symbol_id"]):
                for sp in self.spans_for_symbol(callee["symbol_id"]):
                    if sp.evidence_id not in seen and len(sp.text) >= MIN_SPAN:
                        sp.how = f"graph: {s['qualified_name'].split('.')[-1]} CALLS {callee['qualified_name'].split('.')[-1]}"
                        seen.add(sp.evidence_id); spans.append(sp)

        def fit(candidates):
            """Apply the budget. A span that cannot fit is skipped, not
            truncated: the model must see exactly the bytes it will cite."""
            nonlocal kept, total
            for sp in candidates:
                if len(kept) >= limit:
                    return
                if total + len(sp.text) > max_chars:
                    skipped.append(sp)
                    continue
                kept.append(sp); total += len(sp.text)

        kept: list[EvidenceSpan] = []
        skipped: list[EvidenceSpan] = []
        total = 0
        fit(spans)

        # 3. lexical fallback. It is judged on what SURVIVED the budget, not on
        #    what was collected: a single oversized class body used to fill the
        #    candidate list, get dropped by the cap, and suppress the fallback,
        #    leaving nothing at all.
        if len(kept) < 2:
            fit([sp for sp in self.search(question, limit=limit)
                 if sp.evidence_id not in seen])
        self.last_skipped = skipped
        return kept, subjects

    # ── helpers ─────────────────────────────────────────────────────────
    def _span(self, r, how: str, score: float = 0.0) -> EvidenceSpan:
        loc = json.loads(r["locator"])
        return EvidenceSpan(
            r["evidence_id"], r["rel_path"], loc["byte_start"], loc["byte_end"],
            loc.get("line_start"), r["quoted_text"], r["qualified_name"] or "",
            r["kind"] or "", how, score)
