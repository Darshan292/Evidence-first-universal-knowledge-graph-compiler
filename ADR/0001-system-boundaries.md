# ADR-0001: System boundaries

- **Status:** Accepted (Phase 0)
- **Date:** 2026-09-22
- **Supersedes:** none

## Context

The project could plausibly be scoped as anything from "a better RAG" to "a
universal semantic compiler for all media". Without hard boundaries the design
degenerates into a plugin framework with no working path through it.

## Decision

**The system is a single-user, local-first batch compiler with a query interface
and a read-only visualization. It is not a service.**

Concretely, these are **in**:

1. A CLI that compiles a corpus into one SQLite database.
2. A deterministic extraction layer that is the only writer of structural facts.
3. A bounded semantic layer that may only produce evidence-backed claims.
4. A query layer combining exact lookup, BM25 and graph traversal.
5. A local web UI served from disk for graph browsing and evidence inspection.

These are **out**, and rejecting them is the decision:

| Out of scope | Reason |
|---|---|
| Multi-tenant service, auth, RBAC | no second user exists; auth without a threat model is theatre |
| Network API / daemon | the UI reads the same SQLite file; a server adds a lifecycle to manage |
| Distributed indexing | local throughput is unmeasured; distributing an unmeasured workload is guesswork |
| Message broker, queue, workflow engine | the pipeline is a DAG over a work table with `PENDING/RUNNING/DONE` |
| Plugin discovery/registry system | there are three adapters and they are all in-tree |
| Write-back to source files | the compiler never mutates its inputs. Ever. |
| Incremental re-index at sub-file granularity | file-level content hashing is sufficient until measured otherwise |

## The boundary that matters most

**The deterministic layer must be able to run to completion with the semantic
layer entirely absent.** This is not a configuration flag bolted on afterwards;
it is a structural property. The semantic layer *reads* the deterministic
output and *appends* claims. It never mutates deterministic rows and is never
on the critical path of producing a usable graph.

Test: deleting the entire semantic package from the source tree must leave a
system that still compiles a corpus, serves the UI, and passes the
`deterministic-only` acceptance suite. If that ever stops being true, the
boundary has been violated.

## Consequences

**Good.** The zero-cost path is guaranteed by construction rather than by
discipline. Failure of any provider degrades capability, never availability.
Crash recovery is one database's problem.

**Bad.** No collaboration, no shared index, no remote corpora. Multi-user use
would require a genuine redesign of the storage layer — we accept that and
prefer it to building speculative seams now.

**Accepted risk.** If the project later needs a server, the SQLite file becomes
the bottleneck under concurrent writes. We accept this because the alternative
is designing for a user who does not exist.

## Alternatives rejected

- **Client/server from day one** — adds deployment, auth, and a network hop to a
  tool that runs on one laptop. Rejected as architecture by fashion.
- **Library-only, no CLI** — makes the acceptance tests hypothetical; there would
  be no single command that demonstrably produces the milestone.
- **Plugin system for adapters** — three in-tree adapters do not justify
  discovery, entry points, or a registry. Revisit at adapter #5, not before.
