# IDENTITY & SCOPE AUDIT

**Date:** 2026-09-23 · Prompted by Gate 2.5 Finding B.

Every suffix, prefix, basename and case-insensitive comparison in the repository
was classified. **Only semantic identity was made exact** — replacing every
occurrence would have broken text processing and filesystem filtering that are
correct as they stand.

## Classification

| Location | Usage | Class | Action |
|---|---|---|---|
| `claimfirst.py` scope filter (was `rel_path.endswith(scope)`) | decides which artifact satisfies a scope | **semantic identity** | **FIXED** — exact canonical path |
| `claimfirst.find_symbols` scope filter | decides which symbol is in scope | **semantic identity** | **FIXED** — exact canonical path |
| `c2_generalization.py` edge verification (was `to_qn.endswith(...)`) | certifies whether an extracted edge is correct | **semantic identity** (a verifier) | **FIXED** — exact match; result unchanged at 38/38 |
| `pipeline._module_name` (`rel.endswith(".py")`) | strips a file extension | filesystem filtering | keep |
| `pipeline` rel-path construction | artifact identity | **semantic identity** | **FIXED** — normalized once at ingestion |
| `safety.walk_corpus` (`d.startswith(".")`) | skips dot-directories | filesystem filtering | keep |
| `python_backend` (`alias.name == "*"`) | detects a star-import token | exact grammar comparison | keep |
| `retrievers.r0` (`symbol_qname.endswith(bare)`) | ranking boost in candidate discovery | retrieval scoring | keep — lexical heuristics are permitted in discovery, never in support |
| `preprocess.stem` suffix stripping | tokenization | text processing | keep |
| `support.py` `LIKE` clauses | the superseded chunk-first gate, preserved as a historical experiment | historical | keep, unused by the current path |
| `build_gate225_gold.py` (`endswith("pyproject.toml")`) | picks a deliberately wrong file for a negative | gold construction | keep |
| `claimfirst.find_symbols` (`GLOB`, not `LIKE`) | case-sensitive symbol name match | **semantic identity** | already fixed in Gate 2.25 |

## Canonical artifact identity

One authoritative module: `kgc/artifact_identity.py`.

```
canonical_path(p)              corpus-relative, forward slashes, no "./" prefix
is_exact(artifact, scope)      exact, case-sensitive equality
resolve_scope(scope, known)    -> (matches, EXACT | AMBIGUOUS | UNKNOWN)
```

Normalization happens **once**, at ingestion (`pipeline.py`). The support layer
compares already-canonical values. After the simplification pass there is no
second normalizer: `_module_name` now calls `canonical_path` rather than
splitting the path itself.

### Basename policy

A bare basename is a **convenience, never an identity**:

| Scope | Corpus | Result |
|---|---|---|
| `src/utils.py` | 3 files named `utils.py` | `EXACT` → only `src/utils.py` |
| `utils.py` | 3 files named `utils.py` | **`AMBIGUOUS` → `ABSTAIN_AMBIGUOUS`** |
| `svc.py` | exactly one such file | `EXACT` (unambiguous, so it resolves) |
| `nope.py` | no such file | `UNKNOWN` → `ABSTAIN` |

Comparison is **case-sensitive**. The filesystems in use preserve case, and a
case-insensitive comparison would make `Config.py` and `config.py` one identity.

## The compounding defect

Suffix matching was not the whole problem. The ambiguity guard read:

```python
if len(distinct) > 1 and not c.source_scope:      # BEFORE
```

Supplying a scope **disabled** the guard. So `utils.py` matched three artifacts,
skipped the ambiguity check, and one was chosen by row order — the precise
"silently choose one based on ordering" failure. The guard is now unconditional,
and scope is resolved to a single canonical identity before subject resolution
begins.

## Query IR consequence

`FILE_SCOPE` previously matched only a bare basename, so an exact scope was
**inexpressible**: `src/utils.py` was read as `utils.py`. The pattern now
captures directory-qualified paths.
