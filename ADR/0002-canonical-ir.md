# ADR-0002: The canonical intermediate representation

- **Status:** Accepted (Phase 0)
- **Date:** 2026-09-22

## Context

Parsers disagree about everything: units, coordinate systems, whether a "page"
exists, whether offsets are bytes or characters. If parser output reaches the
graph directly, every downstream consumer must know which parser produced each
row, and adding a modality means touching every consumer.

The temptation is a rich universal document model. That is how you get an
abstraction that fits nothing precisely.

## Decision

**The IR is deliberately thin. It is a set of records with a single shared
mechanism — the typed locator — and no behaviour.**

The IR consists of: `Corpus`, `Source`, `Artifact`, `Document`, `Region`,
`CodeSymbol`, `EntityMention`, `CanonicalEntity`, `Claim`, `Evidence`,
`ClaimRelation`, `ProcessingRun`, `WorkItem`.

Three rules define it:

### Rule 1 — A locator is `(kind, payload)`, and `kind` names the parser's real capability

```
byte_range  {byte_start, byte_end, line_start, col_start, line_end, col_end}
pdf_box     {page, x0, top, x1, bottom, char_start, char_end}
docx_para   {paragraph_index, run_index, char_start, char_end}
docx_cell   {table_index, row, col, char_start, char_end}
```

A parser emits the most specific kind it genuinely produces and **never
fabricates a more precise one**. We verified on 2026-09-22 that `python-docx`
exposes no page attribute; therefore no DOCX locator carries a page, and no
amount of downstream convenience justifies inventing one.

> **[AMENDED 2026-09-22]** The review added `csv_cell`, `json_pointer` and
> `xml_path`, and paired every locator kind with a `verification_strength`
> (ADR-0006). This validated Rule 2: three new kinds, **zero schema changes**.

### Rule 2 — Adding a modality adds locator kinds, never columns

M2 introduces `image_box`, `audio_span`, `video_span`. Because the payload is
JSON behind a `kind` discriminator, the schema does not change. Consumers switch
on `kind`; an unknown `kind` degrades to "evidence exists, cannot render
precisely" rather than crashing.

### Rule 3 — The IR carries no semantics and no inference

The IR records *what a parser saw at a location*. It contains no confidence, no
entity typing, no relation semantics. Those live in `Claim`, which is explicitly
downstream and explicitly evidence-bound.

## Why this boundary is necessary (the abstraction ledger)

Per the project's lean-engineering rule, every abstraction must justify itself:

- **Concrete problem now:** three parsers with three incompatible position
  systems must produce evidence that one UI can render and one validator can
  verify.
- **Existing mechanism considered:** passing parser-native objects through, and
  a single flat `(file, line_start, line_end)` locator.
- **Why reuse was insufficient:** a flat line-based locator cannot express a PDF
  bounding box or a DOCX table cell, and would silently coarsen evidence — the
  exact failure the project exists to prevent.
- **Complexity introduced:** one discriminated union and a JSON payload per
  locator.
- **Complexity removed:** every consumer stops knowing about parsers; the
  evidence verifier is one function, not three.
- **What breaks without it:** evidence precision collapses to line numbers, and
  M2 becomes a schema migration touching every table.

## Consequences

**Good.** Adding M2 modalities is additive. The evidence verifier is a single
switch. The UI has exactly four renderers in M1.

**Bad.** JSON payloads are not type-checked by the database. Mitigation: a
validating constructor per locator kind and a round-trip test per parser.

**Accepted.** Cross-modality locator comparison ("is this PDF box the same place
as this DOCX paragraph?") is meaningless by design. Cross-source identity is the
job of entity resolution, not of locators.

## Alternatives rejected

- **Docling's `DoclingDocument` as the IR.** It is a good model, but adopting it
  makes the IR a function of one dependency's release cycle — 217 releases,
  latest four days before this writing — and drags a CUDA/torch stack (measured:
  119 packages) into the core of a local-first tool. Rejected as the *canonical*
  IR; retained as an *optional input adapter* that maps into our IR.
- **One universal offset space (normalise everything to characters).** Destroys
  bbox and cell information irrecoverably.
- **Storing parser objects and resolving lazily.** Makes the database unreadable
  without the exact parser version that wrote it, destroying reproducibility.

---

## Implementation findings (Gate 1, 2026-09-22)

**The IR held.** Three locator kinds (`byte_range`, `ast_node`, `json_pointer`,
plus `xml_path`) were added with **zero schema changes**, which was Rule 2's test.

**One correction.** `reference` began as a table parallel to `claim`, storing
subject/predicate/object/locator again. That was duplication, not a boundary. It
is now a **detail table keyed by `claim_id`** holding only `(to_name, resolution,
reason)` — the resolution quality of a reference-style claim. One fact store.

**Deleted as speculative:** `Modality.DOCUMENT` / `STRUCTURED` (values nothing
could produce) and `ids.region_id` (no producer until a document adapter exists).
`LocatorKind` values for unimplemented modalities are **kept**, because
`Locator.__post_init__` rejects them and a test asserts that rejection — they
are an enforced contract, not speculation.
