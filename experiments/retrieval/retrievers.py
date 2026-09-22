"""R0-R5 retrieval configurations.

This module MUST NOT read gold. Enforced by test_separation in x1_evaluate.py.
All configurations are local and offline: no API, no model download.
"""
from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass

from experiments.retrieval.preprocess import fts_query, tokenize

TOP_K = 10


@dataclass
class Hit:
    chunk_id: str
    rel_path: str
    score: float
    text: str
    via: str          # which mechanism produced it -- kept for diagnosis


class Index:
    """One SQLite database holding every index. No vector server."""

    def __init__(self, chunks, store=None):
        self.chunks = {c.chunk_id: c for c in chunks}
        self.store = store
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute(
            "CREATE VIRTUAL TABLE fts USING fts5(chunk_id UNINDEXED, body,"
            " tokenize='porter unicode61')")
        self.con.executemany(
            "INSERT INTO fts(chunk_id, body) VALUES(?,?)",
            [(c.chunk_id, " ".join(tokenize(c.text)) + " " + c.text) for c in chunks])
        self._lsa = None

    # ---------- R0: exact identifier / path ----------
    def r0(self, query: str, k: int = TOP_K) -> list[Hit]:
        ids = [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", query)
               if ("_" in t and t.isupper()) or any(ch.isupper() for ch in t) or "." in t]
        hits: list[Hit] = []
        for c in self.chunks.values():
            for ident in ids:
                bare = ident.split(".")[-1]
                if re.search(rf"\b{re.escape(bare)}\b", c.text):
                    boost = 2.0 if (c.symbol_qname and c.symbol_qname.endswith(bare)) else 1.0
                    hits.append(Hit(c.chunk_id, c.rel_path, boost, c.text, "R0"))
                    break
        hits.sort(key=lambda h: -h.score)
        return hits[:k]

    # ---------- R1: BM25 ----------
    def r1(self, query: str, k: int = TOP_K) -> list[Hit]:
        q = fts_query(query)
        try:
            rows = self.con.execute(
                "SELECT chunk_id, bm25(fts) s FROM fts WHERE fts MATCH ? ORDER BY s LIMIT ?",
                (q, k)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [Hit(r["chunk_id"], self.chunks[r["chunk_id"]].rel_path, -r["s"],
                    self.chunks[r["chunk_id"]].text, "R1") for r in rows]

    # ---------- R2: R1 + bounded ranked graph expansion (ADR-0007) ----------
    def expand(self, seeds: list[Hit], k: int = TOP_K, edge_kinds=("CALLS", "CONTAINS", "IMPORTS"),
               max_depth: int = 2):
        if self.store is None:
            return [], {"total_reachable": 0, "truncated": False}
        qnames = [self.chunks[h.chunk_id].symbol_qname for h in seeds
                  if self.chunks[h.chunk_id].symbol_qname]
        if not qnames:
            return [], {"total_reachable": 0, "truncated": False}
        placeholders = ",".join("?" * len(qnames))
        kinds = ",".join("?" * len(edge_kinds))
        rows = self.store.con.execute(f"""
            WITH RECURSIVE seed(qn) AS (
              SELECT qualified_name FROM symbol WHERE qualified_name IN ({placeholders})
            ),
            reach(sym, depth) AS (
              SELECT s.symbol_id, 0 FROM symbol s JOIN seed ON s.qualified_name = seed.qn
              UNION
              SELECT cl.object_id, r.depth+1 FROM claim cl JOIN reach r ON cl.subject_id = r.sym
                WHERE cl.object_id IS NOT NULL AND cl.predicate IN ({kinds}) AND r.depth < ?
              UNION
              SELECT cl.subject_id, r.depth+1 FROM claim cl JOIN reach r ON cl.object_id = r.sym
                WHERE cl.predicate IN ({kinds}) AND r.depth < ?
            )
            SELECT s.qualified_name qn, a.rel_path, min(r.depth) d,
                   (SELECT count(*) FROM claim c2 WHERE c2.object_id = s.symbol_id) indeg
              FROM reach r JOIN symbol s ON s.symbol_id = r.sym
              JOIN artifact a USING(artifact_id)
             WHERE r.depth > 0
             GROUP BY s.symbol_id
        """, (*qnames, *edge_kinds, max_depth, *edge_kinds, max_depth)).fetchall()

        total = len(rows)
        scored = []
        for r in rows:
            # hub damping: a symbol referenced from everywhere is weak evidence
            damp = 1.0 / (1.0 + math.log1p(r["indeg"]))
            scored.append((0.6 ** r["d"] * damp, r["qn"], r["rel_path"]))
        scored.sort(reverse=True)
        out = []
        for score, qn, rel in scored[:k]:
            cid = f"{rel}#{qn}"
            if cid in self.chunks:
                out.append(Hit(cid, rel, score, self.chunks[cid].text, "graph"))
        return out, {"total_reachable": total, "truncated": total > k, "returned": len(out)}

    def r2(self, query: str, k: int = TOP_K):
        lex = self.r1(query, k)
        graph, meta = self.expand(lex[:3], k)
        return _merge([(lex, 1.0), (graph, 0.5)], k), meta

    # ---------- R3: dense (LSA: TF-IDF + truncated SVD) ----------
    def _build_lsa(self, dims=64):
        import numpy as np
        ids = list(self.chunks)
        docs = [tokenize(self.chunks[i].text) for i in ids]
        vocab = sorted({t for d in docs for t in d})
        vidx = {t: i for i, t in enumerate(vocab)}
        X = np.zeros((len(ids), len(vocab)), dtype=np.float64)
        for r, d in enumerate(docs):
            for t in d:
                X[r, vidx[t]] += 1.0
        df = (X > 0).sum(axis=0)
        idf = np.log((1 + len(ids)) / (1 + df)) + 1.0
        X = X * idf
        norms = np.linalg.norm(X, axis=1, keepdims=True); norms[norms == 0] = 1
        X = X / norms
        U, S, Vt = np.linalg.svd(X, full_matrices=False)
        d = min(dims, len(S))
        self._lsa = {"ids": ids, "vidx": vidx, "idf": idf, "Vt": Vt[:d],
                     "D": U[:, :d] * S[:d], "np": np}
        Dn = np.linalg.norm(self._lsa["D"], axis=1, keepdims=True); Dn[Dn == 0] = 1
        self._lsa["Dn"] = self._lsa["D"] / Dn

    def r3(self, query: str, k: int = TOP_K) -> list[Hit]:
        if self._lsa is None:
            self._build_lsa()
        L = self._lsa; np = L["np"]
        q = np.zeros(len(L["vidx"]))
        for t in tokenize(query):
            if t in L["vidx"]:
                q[L["vidx"][t]] += 1.0
        q = q * L["idf"]
        n = np.linalg.norm(q)
        if n == 0:
            return []
        qv = (q / n) @ L["Vt"].T
        qn = np.linalg.norm(qv)
        if qn == 0:
            return []
        sims = L["Dn"] @ (qv / qn)
        order = np.argsort(-sims)[:k]
        return [Hit(L["ids"][i], self.chunks[L["ids"][i]].rel_path, float(sims[i]),
                    self.chunks[L["ids"][i]].text, "R3") for i in order if sims[i] > 0]

    def r4(self, query: str, k: int = TOP_K) -> list[Hit]:
        return _merge([(self.r1(query, k), 1.0), (self.r3(query, k), 1.0)], k)

    def r5(self, query: str, k: int = TOP_K):
        lex, dense = self.r1(query, k), self.r3(query, k)
        graph, meta = self.expand((lex + dense)[:3], k)
        return _merge([(lex, 1.0), (dense, 1.0), (graph, 0.5)], k), meta


def _merge(groups, k):
    """Reciprocal-rank fusion: scale-free, no per-run tuning."""
    scores: dict[str, float] = {}
    keep: dict[str, Hit] = {}
    for hits, w in groups:
        for rank, h in enumerate(hits):
            scores[h.chunk_id] = scores.get(h.chunk_id, 0.0) + w / (60 + rank + 1)
            keep.setdefault(h.chunk_id, h)
    out = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
    return [Hit(cid, keep[cid].rel_path, s, keep[cid].text, keep[cid].via) for cid, s in out]
