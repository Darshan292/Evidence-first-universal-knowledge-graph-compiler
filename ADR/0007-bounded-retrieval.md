# ADR-0007: Bounded, ranked neighbourhood expansion

- **Status:** Accepted (Phase 0, adversarial review)
- **Date:** 2026-09-22
- **Amends:** ARCHITECTURE.md §2 (retrieval), EVALUATION_PLAN.md A-17
- **Addresses:** defect D-1 (critical)

## Context

Phase 0 specified "graph neighbourhood expansion" with depth and node caps but
never defined what expansion *returns*. Measured on a realistic power-law graph
(1M nodes / 5M edges, 0.1% hubs holding 35% of in-edges):

| Expansion | Latency | Nodes returned |
|---|---|---|
| 2-hop undirected, ordinary node | 5.1 ms | 1,872 |
| **3-hop undirected, ordinary node** | **184 ms** | **50,197** |
| 2-hop undirected, hub | 99.7 ms | 23,831 |
| 3-hop reverse from hub ("what calls this utility?") | 258 ms | 75,947 |

**The latency is fine. The result is not an answer.** 50,197 nodes has no
information content, and truncating it to an arbitrary 500 would silently discard
99% of a result — violating the project's commitment not to hide what it knows.

**This is engine-independent.** A graph database returns the same 50,197 nodes,
faster. Phase 0's justification of SQLite by "4-hop p95 = 4.2 ms" was measured on
a uniform random graph with ~300-node neighbourhoods and did not surface this.

## Decision

**Unbounded expansion is removed from the system. The only expansion primitive is
bounded, ranked and typed.**

```
expand(focus, edge_kinds, k, max_depth, direction) -> RankedFrontier
```

Five binding rules:

### 1. Relevance is the bound, not depth
Expansion is a scored frontier (weighted BFS / personalised-PageRank style)
returning the **top-K by score**, not everything within depth `d`. `k` is
explicit and always finite. `max_depth` is a safety rail, not the selector.

### 2. Edge-type filtering is mandatory
`edge_kinds` is a required argument with no default. "What calls this?" traverses
`CALLS` only. Untyped expansion is how a code graph becomes a hairball.

### 3. Hub damping
A node's contribution is damped by its degree, so a 1,905-caller utility function
does not flood every neighbourhood it touches. Without damping, every query in a
real codebase converges on `log()` and `config`.

### 4. Truncation is always reported, never silent
Every bounded result carries the true total and the applied bound:
`{"returned": 500, "total_reachable": 50197, "bound": "top-k-relevance", "k": 500}`
The UI renders this as *"showing top 500 of 50,197 by relevance"*. A silent cut
is prohibited.

### 5. The UI expands one frontier at a time
The visualization never issues an unbounded query and never renders a full graph.
Expansion is on demand from a focus node.

## Interaction with the storage decision

This ADR **strengthens** ADR-0004 rather than weakening it. Once expansion is
bounded and ranked, the traversal a graph engine would accelerate is a top-K
frontier over a filtered edge set — which SQLite performs in single-digit
milliseconds at 1M/5M scale. The measured case for a graph engine gets *weaker*,
not stronger.

The ADR-0004 adoption trigger is restated in these terms: **bounded-expansion
p95 > 200 ms sustained on a real workload**, measured after this ADR is
implemented. Unbounded-traversal timings are no longer a valid basis for that
decision, because unbounded traversal no longer exists in the system.

## Abstraction ledger

- **Problem now:** "expand the neighbourhood" had no defined meaning and returns
  useless results at realistic scale.
- **Considered:** depth caps alone (Phase 0); a hard `LIMIT` on rows.
- **Why insufficient:** depth caps do not bound breadth — 3 hops from an
  *ordinary* node reached 50,197 nodes. A bare `LIMIT` returns an arbitrary
  subset with no ranking and no disclosure.
- **Introduced:** one scoring function and a result envelope carrying totals.
- **Removed:** every unbounded query path; the ambiguity about what expansion
  returns.
- **Without it:** the UI is unusable on any real corpus and retrieval silently
  returns noise.

## Consequences

**Good.** Every expansion result is explainable, bounded and honest about what it
omitted. Query latency is bounded by `k`, not by graph topology. The UI has a
single expansion contract.

**Bad.** Ranking introduces a scoring function that must itself be evaluated —
a bad ranker silently hides relevant nodes. Mitigation: `Recall@K` on the
`multi_hop` and `cross_document` eval classes measures exactly this, and the
disclosed `total_reachable` makes over-truncation visible to the user.

**Accepted.** Some genuinely broad questions ("everything transitively touching
payments") cannot be answered as a node list. They are answered as a *summary
with counts* plus a ranked sample — which is the honest shape of that answer.

## Acceptance test (strengthens A-17)

- No expansion API can return an unbounded result set (asserted by contract test).
- Every truncated response carries `total_reachable`.
- Hub expansion at 1M scale returns within the `k` bound and discloses the total.
