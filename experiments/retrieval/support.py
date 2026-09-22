"""Support / abstention gate. Deterministic. Reads no gold.

Implements ABSTENTION_DESIGN.md: a conjunction of necessary conditions, not a
weighted score. Every abstention names the condition that failed.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from experiments.retrieval.preprocess import stem, tokenize

ABSTAIN = "ABSTAIN"
EXPOSE = "EXPOSE"
EXPOSE_CONFLICTED = "EXPOSE_CONFLICTED"

_NUM = re.compile(r"\b\d+(?:\.\d+)?\b")
_QUOTED = re.compile(r"'([^']+)'|\"([^\"]+)\"")
_RELATION_VERB = re.compile(
    r"\b(call|calls|called|import|imports|imported|use|uses|used|define|defines|defined)\b",
    re.I)


@dataclass
class Decision:
    outcome: str
    reason: str
    candidate_id: str | None = None
    evidence_text: str | None = None
    failed_condition: str | None = None
    detail: dict = field(default_factory=dict)


class SupportGate:
    """Conditions S1-S5 from ABSTENTION_DESIGN.md.

    `conditions` selects the design: G0 (baseline), G1, G2, G3.
    """

    DESIGNS = {
        "G0": ("baseline",),                    # Gate 1.5 grounded() rule
        "G1": ("S1", "S2"),
        "G2": ("S1", "S2", "S3"),
        "G3": ("S1", "S2", "S3", "S4"),
        # G4 repairs three defects diagnosed on TEST run #2 and is therefore
        # POST-HOC: its TEST result is reported as indicative, never as binding.
        #   * S2 compares stemmed tokens (query 'imports' vs source 'import')
        #   * S2 requires ALL distinctive query terms in ONE candidate, because
        #     matching 'apac' in routing.xml is not support for a fees.csv question
        #   * S5 only counts numeric disagreement near a shared distinctive term
        "G4": ("S1", "S2all", "S3", "S4"),
    }

    def __init__(self, chunks, store=None, design="G3", idf_percentile=80):
        self.chunks = {c.chunk_id: c for c in chunks}
        self.store = store
        self.design = design
        self.conditions = set(self.DESIGNS[design])
        self.idf = self._build_idf()
        self.set_threshold(idf_percentile)
        self.symbol_names = self._load_symbols()

    # ---------- corpus statistics ----------
    def _build_idf(self) -> dict[str, float]:
        n = len(self.chunks) or 1
        df: dict[str, int] = {}
        for c in self.chunks.values():
            for t in set(tokenize(c.text)):
                df[t] = df.get(t, 0) + 1
        return {t: math.log(n / d) for t, d in df.items()}

    def set_threshold(self, percentile: int):
        """θ: the percentile, applied to the QUERY's own term IDFs.

        The first implementation took the percentile over the whole corpus
        vocabulary. 46.3% of this corpus's vocabulary is hapax, so p60 landed on
        the maximum IDF and "distinctive" collapsed to "appears in exactly one
        chunk" -- excluding ordinary query terms like `payment` (IDF 1.9). The
        gate abstained on every positive. Query-relative is what the design
        specified: among THIS query's terms, the most distinctive ones.
        """
        self.idf_percentile = percentile

    def _load_symbols(self) -> dict[str, str]:
        if self.store is None:
            return {}
        out = {}
        for r in self.store.con.execute("SELECT name, qualified_name FROM symbol"):
            out.setdefault(r["name"].lower(), r["qualified_name"])
        return out

    # ---------- conditions ----------
    def _s1_evidence_verifiable(self, chunk) -> bool:
        """A candidate must carry a localized span that re-derives from source."""
        return bool(chunk.text) and chunk.byte_end >= chunk.byte_start

    def _query_distinctive(self, query) -> set[str]:
        """The query's own most distinctive terms, relative to this query."""
        qt = sorted({t for t in tokenize(query)})
        if not qt:
            return set()
        scored = sorted(qt, key=lambda t: self.idf.get(t, math.inf))
        cut = min(len(scored) - 1, int(len(scored) * self.idf_percentile / 100))
        floor = self.idf.get(scored[cut], math.inf)
        return {t for t in qt if self.idf.get(t, math.inf) >= floor}

    def _s2_distinctive_anchor(self, query, chunk, require_all=False) -> tuple[bool, dict]:
        distinctive = {stem(t) for t in self._query_distinctive(query)}
        if not distinctive:
            return False, {"distinctive_terms": [], "matched": []}
        ct = {stem(t) for t in tokenize(chunk.text)}
        matched = sorted(distinctive & ct)
        missing = sorted(distinctive - ct)
        ok = (not missing) if require_all else bool(matched)
        return ok, {"distinctive_terms": sorted(distinctive), "matched": matched,
                    "missing": missing}

    def _s3_literals_present(self, query, chunk) -> tuple[bool, dict]:
        """Every numeric / quoted literal in the query must appear in the evidence."""
        nums = set(_NUM.findall(query))
        quoted = {a or b for a, b in _QUOTED.findall(query)}
        literals = nums | quoted
        if not literals:
            return True, {"literals": []}
        present = {l for l in literals if re.search(rf"\b{re.escape(l)}\b", chunk.text)}
        missing = sorted(literals - present)
        return not missing, {"literals": sorted(literals), "missing": missing}

    def _s4_relation_grounded(self, query) -> tuple[bool, dict]:
        """If the query asserts a relation between two corpus symbols, the graph
        must contain a DERIVED edge between them."""
        if self.store is None or not _RELATION_VERB.search(query):
            return True, {"applied": False}
        toks = {t for t in tokenize(query, split_ids=False)}
        named = [self.symbol_names[t] for t in toks if t in self.symbol_names]
        named = sorted(set(named))
        if len(named) < 2:
            return True, {"applied": False, "named": named}
        rows = self.store.con.execute(f"""
            SELECT count(*) n FROM claim cl
              JOIN symbol s1 ON s1.symbol_id = cl.subject_id
              JOIN symbol s2 ON s2.symbol_id = cl.object_id
             WHERE cl.establishment='DERIVED'
               AND ( (s1.qualified_name LIKE ? AND s2.qualified_name LIKE ?)
                  OR (s1.qualified_name LIKE ? AND s2.qualified_name LIKE ?) )
        """, (f"%{named[0]}%", f"%{named[1]}%", f"%{named[1]}%", f"%{named[0]}%")).fetchone()
        return rows["n"] > 0, {"applied": True, "named": named, "edges": rows["n"]}

    def _s5_conflict(self, query, supported) -> tuple[bool, dict]:
        """Numeric disagreement among supported candidates that share the query's
        distinctive vocabulary. Deterministic; reads the candidates only."""
        distinctive = {stem(t) for t in self._query_distinctive(query)}
        values: dict[str, set] = {}
        for ch in supported:
            toks = [stem(t) for t in tokenize(ch.text)]
            raw = tokenize(ch.text)
            for i, tok in enumerate(toks):
                if tok not in distinctive:
                    continue
                # only numbers NEAR a shared distinctive term count as an answer
                # candidate; otherwise an ADR number or an unrelated constant
                # registers as disagreement (defect seen on TEST run #2)
                window = raw[max(0, i - 6): i + 7]
                for w in window:
                    if w.isdigit() and len(w) <= 4:
                        values.setdefault(w, set()).add(ch.rel_path)
        conflicting = {v: p for v, p in values.items() if p}
        return len(conflicting) > 1, {"values": sorted(conflicting)}

    # ---------- decision ----------
    def decide(self, query: str, hits) -> Decision:
        if self.design == "G0":
            if not hits:
                return Decision(ABSTAIN, "no candidates", failed_condition="baseline")
            qt = set(tokenize(query))
            top = self.chunks[hits[0].chunk_id]
            if qt & set(tokenize(top.text)):
                return Decision(EXPOSE, "baseline: query term overlaps top hit",
                                hits[0].chunk_id, top.text)
            return Decision(ABSTAIN, "baseline: no term overlap", failed_condition="baseline")

        if not hits:
            return Decision(ABSTAIN, "no candidates", failed_condition="S0")

        ok_s4, d4 = (self._s4_relation_grounded(query) if "S4" in self.conditions
                     else (True, {"applied": False}))
        if not ok_s4:
            return Decision(ABSTAIN,
                            f"asserted relation between {d4['named']} has no DERIVED edge",
                            failed_condition="S4", detail=d4)

        supported, first_fail = [], None
        for h in hits:
            ch = self.chunks[h.chunk_id]
            if "S1" in self.conditions and not self._s1_evidence_verifiable(ch):
                first_fail = first_fail or "S1"; continue
            if "S2" in self.conditions or "S2all" in self.conditions:
                ok, d2 = self._s2_distinctive_anchor(
                    query, ch, require_all="S2all" in self.conditions)
                if not ok:
                    first_fail = first_fail or ("S2all" if "S2all" in self.conditions else "S2")
                    continue
            if "S3" in self.conditions:
                ok, d3 = self._s3_literals_present(query, ch)
                if not ok:
                    first_fail = first_fail or "S3"; continue
            supported.append(ch)

        if not supported:
            return Decision(ABSTAIN, f"no candidate satisfied {first_fail or 'the conditions'}",
                            failed_condition=first_fail or "S2")

        conflicted, d5 = self._s5_conflict(query, supported)
        best = supported[0]
        if conflicted:
            return Decision(EXPOSE_CONFLICTED,
                            f"supported but sources disagree: {d5['values']}",
                            best.chunk_id, best.text, detail=d5)
        return Decision(EXPOSE, "all conditions satisfied", best.chunk_id, best.text,
                        detail={"design": self.design, "theta_percentile": self.idf_percentile})
