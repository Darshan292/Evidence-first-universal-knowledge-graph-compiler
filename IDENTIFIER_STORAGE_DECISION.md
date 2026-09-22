# C-1: Identifier storage decision

**Status:** RESOLVED — adopted 128-bit hex TEXT. **Date:** 2026-09-22
**Experiment:** `experiments/c1_identifier_storage.py` · **Raw:** `experiments/c1_results.json`

## Current representation (before)

64-character hexadecimal TEXT (full SHA-256), used for every identifier except
`content_sha256`.

## Measured cost

Synthetic workload: 20,000 symbols, 32,000 claims, 32,000 evidence rows, same
indexes as the real schema.

| Representation | DB | Index | Index % | Insert | Rows/s | PK lookup | Subject scan | Audit | vs TEXT-64 |
|---|---|---|---|---|---|---|---|---|---|
| **TEXT hex64** (before) | 51.95 MB | 19.75 MB | 38.0% | 1.40 s | 82,830 | 3.9 µs | 6.1 µs | 9.5 ms | 1.000 |
| **TEXT hex32** (adopted) | **29.90 MB** | 10.96 MB | 36.7% | **0.55 s** | **212,481** | 4.2 µs | 6.3 µs | 9.2 ms | **0.576** |
| BLOB 32 | 31.06 MB | 10.96 MB | 35.3% | 0.77 s | 151,034 | 5.7 µs | 6.8 µs | 10.3 ms | 0.598 |
| BLOB 16 | 19.81 MB | 6.76 MB | 34.1% | 0.57 s | 203,621 | 4.2 µs | 6.2 µs | 10.9 ms | 0.381 |

**On the real pipeline** (whole repository ingested): **6.36 MB → 4.31 MB,
32.2% smaller.** Less than the synthetic 42% because a real database carries
more source text, which is unaffected by identifier width.

## Alternatives considered

**BLOB 16** is the smallest (62% reduction) and was the obvious candidate. It was
**rejected**, and not on performance grounds — its lookup latency is fine:

1. **It breaks the evidence_ids JSON array.** `claim.evidence_ids` is a JSON
   array that participates in the deterministic `claim_id` hash and is read by
   the SQLite triggers via `json_each`. JSON has no byte type, so every id would
   have to be hex-encoded back into the JSON anyway — paying the TEXT cost in
   the one column where the invariant triggers need it.
2. **Migration cost is qualitatively different.** TEXT-32 is a one-line change in
   `ids.py`. BLOB-16 touches `ids.py`, `store.py`, `ir.py`, `evidence.py`,
   `mapper.py` and `pipeline.py`, and every parameter binding.
3. **Debugging ergonomics.** A `sqlite3` shell prints TEXT ids directly; BLOBs
   print as raw bytes and need `hex()` wrapped around every column in every
   ad-hoc query. For a system whose entire value is inspectable provenance, that
   is a real ongoing cost, not an aesthetic one.

**BLOB 32** is strictly worse than TEXT 32 here: nearly the same size (31.06 vs
29.90 MB), slower inserts, slower lookups, and all of BLOB's ergonomic costs.

## Decision

**Adopt 128-bit identifiers, hex-encoded as TEXT (32 characters).**
One constant in `kgc/ids.py`; `content_sha256` deliberately stays at the full 64
characters because it is the artifact's *content identity*, not an internal key.

## Reason

It captures **58% of the available saving for a one-line change**, with no
measurable latency cost (PK lookup 3.9 → 4.2 µs), a **2.5× insert throughput
gain**, and zero loss of inspectability. BLOB-16's extra 10 MB at this scale does
not justify touching six modules and breaking the JSON the invariant triggers
depend on.

**Collision safety:** 128 bits gives a 50% collision probability at roughly 2⁶⁴
(1.8 × 10¹⁹) identifiers. The largest corpus this project contemplates is many
orders of magnitude below that.

## What would reopen this

A measured corpus where the database exceeds ~50 GB, at which point the further
38% that BLOB-16 offers would be worth the migration. Nothing observed so far is
within three orders of magnitude of that.
