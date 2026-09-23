# OCCURRENCE IDENTITY AUDIT (K-1.1 §3)

Every place in the repository where a qualified name is used as a lookup key,
and what was decided about it.

## The distinction being enforced

| | meaning | may be duplicated? |
|---|---|---|
| **qualified name** | a NAME. Human-readable, used for query matching, module-level corpus resolution and ambiguity detection. | **yes** — 493 names cover 1,302 symbols in werkzeug |
| **`symbol_id`** | a concrete SOURCE OCCURRENCE: artifact + qualified name + kind + byte span. | no |

A name is only an identity when the artifact defines it exactly once. Where it
does not, the occurrence is selected by **source containment**, and where
containment cannot select one, nothing is attached.

## Measured before the fix

Re-derived from source with the pre-K-1.1 `by_qname[qname] = symbol_id` rule
(last occurrence wins):

| corpus | files with duplicate qnames | duplicate qnames | children given a parent that does not contain them | reference sites attributed to a symbol that does not contain them |
|---|---|---|---|---|
| corpus1 | 0 / 7 | 0 | 0 | 0 |
| corpus2 | 4 / 8 | 20 | 0 | 0 |
| **corpus3** | **88 / 140** | **493** | **18** | **50** |

The 18 wrong parents are also what destroyed seven artifacts: a forward
`parent_id` violates `symbol.parent_id REFERENCES symbol(symbol_id)`, and the
resulting rollback removed the artifact row with everything else. The 50 wrong
reference subjects committed silently — wrong `CALLS`/`IMPORTS`/`EXTENDS`
subjects in a graph reported at deterministic precision 1.000.

## Every lookup found, and its disposition

### Fixed — occurrence identity now decides

| Site | Was | Now |
|---|---|---|
| `mapper.map_analysis` — parent link | `by_qname[parent_qname]`, last occurrence wins | `OccurrenceIndex.enclosing()`: the occurrence whose byte span **contains** the child. No unique one → no parent, plus an `UNRESOLVED_PARENT_OCCURRENCE` diagnostic |
| `mapper.map_analysis` — `CONTAINS` subject and object | both by name | subject is the enclosing occurrence, object is **this exact child's** id |
| `mapper.map_analysis` — reference subject (`RawReference.from_qname`) | `by_qname[from_qname]` | `OccurrenceIndex.owner()`: enclosing occurrence, else the sole occurrence, else dropped with an `UNRESOLVED_REFERENCE_SUBJECT` diagnostic |
| `mapper.map_analysis` — reference target (`to_qname`) | `by_qname` then corpus-wide, first hit wins | local definition only when the artifact defines it **once**; several → recorded `UNRESOLVED` with the reason, never a guess |
| `resolver.ModuleIndex.from_store` — `symbol_ids` | `ids.setdefault(qname, sid)` over an **unordered** scan | a name covering several symbols maps to `None`; the scan is `ORDER BY symbol_id`. Absent = "not in the corpus"; present-and-`None` = "several candidates". Removed the last 2 arbitrary cross-artifact `CALLS` edges on werkzeug |

### Why the parent rule and the subject rule differ

A parent link **asserts** lexical containment, so containment is definitional
there and is enforced strictly — `check_invariants()` fails if any parent does
not contain its child.

A reference subject asserts something else: *this call site belongs to symbol
S*. Containment is only the disambiguator among same-named candidates. A
decorator's call site sits **above** its function's own byte span, because
`FunctionDef.lineno` points at `def`, not at `@`. Demanding containment there
dropped **283 real edges** on werkzeug. So `owner()` falls back to the sole
occurrence when the name is unambiguous — there is nothing to choose between.

Audited on the repaired graph: 16,244 claims have evidence inside their
subject's span, 283 lie outside but have a uniquely-named subject, and **0** lie
outside a subject whose name is ambiguous.

### Deliberately left as name lookups

These treat a qualified name as a name, which is what it is.

| Site | Why it is correct |
|---|---|
| `resolver.ModuleIndex.modules` (`module -> {local name -> qname}`) | Namespace resolution. Values are qnames, not ids, so a duplicate write stores the identical value. Ambiguity is resolved downstream by the rule above. |
| `resolver._resolve_call` / `_resolve_import` | Decide *which module* a name comes from. They produce a qname; turning it into an id is the mapper's job. |
| `ids.symbol_id(artifact, qname, kind, start, end)` | The span is already in the identifier. This is what makes occurrence identity expressible at all. |
| `store` schema: `i_symbol_qn`, `symbol.qualified_name` | A human-readable column and its index. Identity is `symbol_id`, the primary key. |
| `cli.py` symbol lookup | Prints **every** matching symbol with its id. Shows ambiguity rather than hiding it. |
| `claimfirst.find_symbols` | Returns **all** matches; `decide()` abstains with `ABSTAIN_AMBIGUOUS` when more than one distinct qualified name survives. Ambiguity detection is the feature. |
| `claimfirst.direct_children` | Keyed on `parent_id`, an id, not a name. Correct already, and now correct *about the right occurrence*. |
| `retrievers.py` seed join on `qualified_name` | Retrieval expansion by name. Out of scope (K-1.1 §16 forbids touching retrieval); noted below. |
| `support.py` `out.setdefault(name.lower(), qualified_name)` | A chunk-first alias map, first hit wins. Part of the baseline being measured *against*, not of the claim graph. Out of scope. |
| `chunker.py` symbol grouping | Groups by path for chunk boundaries; the qname is a label. |

### Recorded, not fixed

| Item | Status |
|---|---|
| `retrievers.py` / `support.py` name expansion can reach several occurrences of a name | Retrieval-side, explicitly out of scope for K-1.1. It cannot corrupt the claim graph — it only widens a candidate set that the support layer then filters and, on ambiguity, abstains on. Belongs with J-2. |
| `kgc/ir.py`: `STRUCTURAL_PREDICATES`, `SEMANTIC_PREDICATES`, `STRUCTURAL_TO_SEMANTIC` | **Verified genuinely dead** (§17). An AST-level scan of every `.py` in the repository finds exactly one read, `ir.py:88` building `STRUCTURAL_TO_SEMANTIC` from `STRUCTURAL_PREDICATES`; nothing outside `ir.py` reads any of the three. They are not part of the active trust boundary — that is `kgc.predicates.is_trusted` plus the `structural_predicate_requires_derivation` trigger, whose list is derived from `kgc.predicates.STRUCTURAL`. `STRUCTURAL_TO_SEMANTIC` encodes ADR-0006's model-predicate rewrite, a policy that is **documented but not implemented**. Deferred technical debt: deleting it discards written policy, and implementing it is LLM work nobody has authorised. Not touched. |

## Executable guarantees

Added to `Store.check_invariants()`, so the whole database is re-checked
independently of the write path:

* no symbol whose parent is in another artifact, is not a qualified-name prefix,
  or does not contain it in source;
* no claim whose subject or object symbol does not exist;
* no claim belonging to a `FAILED` artifact;
* no `FAILED` artifact without a diagnostic.

`experiments/k11_integrity_audit.py` re-derives parent and subject correctness
from persisted byte spans, independently of the mapper that wrote them.
