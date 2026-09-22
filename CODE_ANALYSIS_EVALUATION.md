# C-3 / C-2: Code analysis evaluation

**Status:** RESOLVED. **Date:** 2026-09-22
**Experiments:** `experiments/c2_resolution_eval.py`, `experiments/c3_code_analysis.py`
**Gold:** `eval/CODE_ANALYSIS_GOLD.json` (v1.0.0), `eval/malformed/gold.json` (v1.0.1)

The question this answers is not "Tree-sitter: yes or no." It is: **what level of
deterministic code analysis must the system guarantee before its graph is a
meaningful evaluation substrate?**

---

## A. Valid Python

stdlib `ast` establishes, deterministically and by construction (it is the
normative CPython grammar): module/class/function/method definitions, byte and
line ranges, docstrings, imports, module-level assignments, call sites, and
intra-module scope binding.

It cannot establish types. Python is dynamically typed; claiming otherwise would
be fabrication.

## B. Cross-module resolution (C-2)

A corpus-level resolver (`kgc/analysis/resolver.py`) was implemented after the
baseline measurement triggered pre-registered rule D-F.

### Measured against independently-authored gold (13 call sites)

| Metric | Before resolver | After resolver |
|---|---|---|
| Deterministic recall (9 gold sites) | **0.222** | **1.000** |
| Unresolved-correct rate (3 gold sites) | **0.000** | **1.000** |
| False-positive targets | 0 | **0** |
| Under-resolved | 7 | 0 |

All 13 cases pass: direct import, aliased import, `from … import … as`, plain
from-import, intra-module local, same-name symbol in a sibling module,
re-export, **shadowed name**, absent third-party package, absent third-party
symbol, both sides of a circular import, and a stdlib call.

The two that matter most are the traps, and both are correct:

- **CS-06** resolves `from_import.run -> pkg.core.shared_name`, *not* the
  identically-named `pkg.other.shared_name`.
- **CS-08** resolves `shadowed.run -> shadowed.helper` (the local definition),
  *not* the imported `pkg.core.helper`.

Pre-registered veto **D-H** (a single wrong deterministic target rejects the
change regardless of recall) is satisfied: zero false positives.

### Two defects the measurement exposed

1. **Over-classification.** Before the resolver, `requests.get`, `os.path.join`
   and an absent third-party symbol were labelled `HEURISTIC` because the head
   name was imported. The corpus-level pass now correctly reports `UNRESOLVED`:
   we know the name, we cannot reach a definition, and claiming otherwise
   overstates what was established.
2. **`DETERMINISTIC` with a null target.** The first resolver implementation
   upgraded the resolution class but the mapper could only link symbols *within
   one artifact*, so cross-module targets resolved to nothing. A new invariant
   now rejects this at the database level:
   *"N DETERMINISTIC reference(s) with no resolved target."*

### Effect on real code

| Corpus | DETERMINISTIC | HEURISTIC | UNRESOLVED | Cross-file CALLS edges |
|---|---|---|---|---|
| self (repo), before | 11.2% | 32.7% | 56.1% | **0** |
| self (repo), after | **18.5%** | 6.4% | 75.1% | **167** |
| orderflow, after | 23.5% | 29.4% | 47.1% | 5 |

`UNRESOLVED` *rose* because imports that were previously flattered as
`HEURISTIC` are now correctly reported as unreachable. **A higher unresolved rate
here is an improvement in honesty, not a regression.**

### Trap found: module naming is root-relative

Ingesting `kgc/` names modules `store`, `ids`; the code's imports say
`kgc.store`, so **nothing matches and every cross-module edge is lost silently**.
Ingesting the repository root produces 167 cross-file edges; ingesting the
package directory produces 0. This is a real usability defect, recorded as a
known limitation — the system should detect and warn, which it does not yet.

## C. Syntax errors — what survives

Five malformed files with independently-enumerated recoverable symbols
(9 symbols total):

| Approach | Recall | Precision | Flags every malformed file |
|---|---|---|---|
| stdlib `ast` | **0.000** | n/a (recovers nothing) | yes — raises `SyntaxError` |
| Tree-sitter 0.26.0 | **1.000** | 0.750 | **no — 2 of 5 not flagged** |

**The decisive finding is the last column.** Tree-sitter reports
`has_error = False` for:

- `m4_py2_print.py` — a Python-2 `print "…"` statement, which is **not valid
  Python 3**, and
- `m3_bad_indent.py` — a file CPython rejects on indentation.

A system using Tree-sitter as its validity oracle would mark both `OK` and emit
their symbols as `DETERMINISTIC`. That is exactly the class of silent
over-claiming this project exists to prevent.

**On the 0.750 precision — an honest caveat.** The three "spurious" symbols are
`broken`, `head_fn`, `bad_indent`: definitions that exist *textually* but whose
bodies are corrupt. Tree-sitter is not fabricating them. The number reflects my
gold's stricter definition of "recoverable", not a Tree-sitter error, and should
not be read as a fabrication rate.

## D. Unsupported language

**Defect found and fixed.** A `.js` or `.java` file previously produced
`seen: 0` — not a misleading empty graph but **no record whatsoever**. The
coverage report would have claimed complete success on a corpus it had silently
ignored.

The router now walks every file and records unanalysable ones explicitly:

```
Main.java  parse_status=UNSUPPORTED  "no analyser for language 'java'"
probe.js   parse_status=UNSUPPORTED  "no analyser for language 'javascript'"
```

---

## The minimum deterministic analysis contract for the first release

The first release **guarantees**, for Python only:

1. Every file is accounted for as `OK`, `FAILED`, `SKIPPED` or `UNSUPPORTED` —
   never absent.
2. For `OK` files: definitions, containment, imports, docstrings and call sites,
   each with a byte-exact evidence span, established under the normative CPython
   grammar.
3. Every reference carries `DETERMINISTIC`, `HEURISTIC` or `UNRESOLVED`, and an
   `UNRESOLVED` reference is recorded with its surface name and a reason — never
   omitted and never guessed.
4. Cross-module resolution within the analysed corpus, including alias, from-,
   re-export, circular and shadowing cases, with **zero** wrong deterministic
   targets on the gold set.
5. A symbol outside the analysed corpus (stdlib, third-party) is `UNRESOLVED`,
   not `HEURISTIC`.

The first release **does not** claim: type information, dynamic dispatch, or any
language other than Python.

## What must be added before claiming multi-language code intelligence

1. **A second backend.** One language is not evidence of language independence;
   ADR-0005's interface is untested against a second implementation.
2. **Tree-sitter in a specific role** — as a *recovery* mechanism, never as the
   validity oracle. `ast` decides whether a file is valid Python; Tree-sitter
   recovers symbols from files `ast` rejects, and those symbols must enter at a
   lower establishment than `DERIVED` because the normative grammar rejected the
   file. Adopting it the other way round is what the measurement forbids.
3. **Per-language capability declaration.** ADR-0005's matrix must be populated
   by measurement per backend, not asserted.
4. **SCIP or compiler-backed indexing** for any language where cross-module
   resolution genuinely needs type information — not yet demonstrated as needed
   for Python, whose gold set the `ast`-based resolver satisfies completely.

## Decisions against the pre-registered rules

- **D-F (cross-module resolver):** triggered at 0.222 < 0.80 → implemented →
  now 1.000. **Resolved.**
- **D-G (Tree-sitter):** adoption requires materially improving a guarantee
  `ast` cannot provide, *with correct symbols*. It recovers 1.000 where `ast`
  recovers 0.000 — genuinely material — **but** it fails to flag 2 of 5 invalid
  files, so it cannot be the validity oracle. **Adopted conditionally, for the
  recovery role only, and deferred until a measured parse-failure rate on a real
  corpus justifies the dependency.** Current measured failure rate on this
  repository: 0%.
- **D-H (false-positive veto):** satisfied — zero wrong deterministic targets.
