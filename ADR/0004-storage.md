# ADR-0004: Storage

- **Status:** Accepted (Phase 0)
- **Date:** 2026-09-22
- **Note:** This ADR **overrides the bootstrap spec's suggestion** of LadybugDB
  as the primary store plus SQLite plus Qdrant. Reasoning and measurements below.

## Context

The bootstrap spec proposed three stores: LadybugDB (graph), SQLite (jobs,
metadata), Qdrant (vectors). That is three consistency domains, two servers'
worth of lifecycle, and — critically — no way to commit a claim and its evidence
atomically.

## Investigation (all executed 2026-09-22)

### LadybugDB smoke test

Installed `ladybug==0.20.4` (MIT) and executed against it:

| Capability | Result |
|---|---|
| `CREATE NODE TABLE` / `CREATE REL TABLE` | **works** |
| Cypher `MATCH`, property filters | **works** |
| Variable-length paths `[:CALLS*1..3]` | **works** |
| `BEGIN` / `ROLLBACK`, verified rolled back | **works** |
| Single-file `.lbug` on disk | **works** |
| `INSTALL fts` / `INSTALL vector` | **FAILS — downloads a binary from `extension.ladybugdb.com` at runtime** |
| `CREATE_FTS_INDEX`, `CREATE_VECTOR_INDEX` | unavailable without that download |

The engine is real and works. But **full-text search and vector indexing are not
in the engine** — they are per-platform, per-version binaries fetched from a
vendor CDN on first use. For a tool whose first constraint is offline, zero-key
operation, putting retrieval behind a runtime download from a third-party host is
a availability and supply-chain dependency we will not accept in the core path.

### SQLite capability check

stdlib `sqlite3`, SQLite 3.45.1: **FTS5 compiled in**, `bm25()` ranking, porter
stemming, phrase / boolean / prefix queries — all verified working. Zero
dependencies, zero download, fully offline.

### Traversal benchmark

50,000 nodes / 250,000 edges, recursive CTE, indexed `edge(src)`:

| Hops | p50 | p95 |
|---|---|---|
| 1 | 0.02 ms | 0.04 ms |
| 2 | 0.08 ms | 0.15 ms |
| 3 | 0.34 ms | 0.58 ms |
| 4 | 1.63 ms | 4.23 ms |

Database file: 12.9 MB. Exact identifier lookup: 0.063 ms.

## Decision

**One SQLite file is the single store of record.** Nodes, edges, claims,
evidence, entity resolution, job state, the usage ledger and the FTS5 index all
live in it.

The decisive argument is not performance — it is atomicity:

> A claim and its evidence must commit or fail together. Split across two
> engines, a crash between commits produces an evidence row with no claim, or a
> claim whose evidence never landed. In a system whose entire value proposition
> is "every claim has evidence", that seam is a correctness defect. One file
> means one transaction and one fsync.

Performance merely confirms there is no reason to pay that price: a 4-hop
traversal at p95 = 4.2 ms is roughly 50× under any interactive threshold.

## Adoption triggers (measurable, not aspirational)

LadybugDB is not rejected — it is **deferred with conditions**, and it has
already been validated so the path is known. Add it as a *derived, rebuildable
projection* (never a second source of truth) when **any** of:

- graph traversal p95 exceeds 200 ms on the real workload, or
- the graph exceeds ~5M edges, or
- a query genuinely requires Cypher expressiveness that recursive CTEs cannot
  express readably (e.g. weighted shortest-path over typed edges).

Add a vector store when, and only when, the eval measures BM25 + graph expansion
below the Recall@10 target on the semantic/paraphrase query class.

## Storage boundary — a boundary, not a framework

All SQL lives in one module, `store.py`, exposing named operations
(`add_artifact`, `add_claim_with_evidence`, `expand_neighbourhood`, …). The rest
of the system never writes SQL.

This is deliberately **not** a storage-adapter framework: no `BaseStore`, no
registry, no second implementation written speculatively. One module with a
named surface gives the replaceability the project asked for; if the engine ever
changes, one file is rewritten. A second implementation nobody runs would rot.

## Consequences

**Good.** Zero-install storage (stdlib driver). One transaction domain. One file
to back up, copy, diff or ship as a reproducible artifact. Crash recovery is
SQLite's WAL, the best-tested implementation available to us.

**Bad.** No Cypher. Multi-hop queries are recursive CTEs, which are wordier. We
accept verbosity in one module over a distributed consistency problem.

**Accepted risk.** Single-writer concurrency. The compiler is a batch process
with one writer by design (ADR-0001).

**Risk avoided.** We are not betting the store of record on a pre-1.0 (`0.20.4`)
community fork, ~10 months old, of a project its corporate sponsor abandoned
after an acquisition. That is not a criticism of LadybugDB — it is active and
the fork appears healthy — it is a statement about what belongs on the critical
path of a system whose premise is durability of evidence.
