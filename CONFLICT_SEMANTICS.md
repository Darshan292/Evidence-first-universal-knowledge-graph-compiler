# CONFLICT SEMANTICS

**Status:** Implemented in `kgc/claim_value.py`. **Date:** 2026-09-23

## 1. The defect this replaces

Conflict was previously computed as `evidence_text != evidence_text`. Different
wording is not contradiction:

| A | B | Old verdict | Correct |
|---|---|---|---|
| `timeout = 30 seconds` | `gateway timeout defaults to 30 s` | CONFLICT ✗ | **agree** |
| `timeout = 30 seconds` | `timeout = 60 seconds` | CONFLICT ✓ | **contradict** |

The old rule also read the ADR number `007` as a disagreeing timeout value.

## 2. Conflict operates on claim semantics

Normalized comparison over: subject, predicate, object, literal value, scope,
temporal context.

```
same normalized value       → SUPPORTS / DUPLICATE
different, both in-domain   → CONTRADICTS
outside the closed domain   → UNRESOLVED   (never guessed)
```

## 3. The closed domain

The normalizer handles **only**:

| Kind | Handling |
|---|---|
| numbers | integers and decimals |
| units | `ms, s, min, h` converted to a base; `byte` recognised |
| strings | quoted content, byte/raw/f prefixes stripped |
| identifiers | bare tokens including hyphens (`django-concat`) |
| booleans | `true/false`, and `none/null` as a distinct kind |
| symbols | canonical symbol id |

**Anything else returns `UNRESOLVED`.** Explicitly out of scope: prose
equivalence, semantic paraphrase, arithmetic beyond unit conversion, and
multi-valued expressions. Two numbers in one string returns `UNRESOLVED` rather
than picking one.

## 4. Verified behaviour

| A | B | Result |
|---|---|---|
| `timeout = 30 seconds` | `gateway timeout defaults to 30 s` | `SAME` |
| `timeout = 30 seconds` | `timeout = 60 seconds` | `DIFFERENT` |
| `0.5 min` | `30 s` | `SAME` (unit conversion) |
| `b"itsdangerous"` | `"itsdangerous"` | `SAME` |
| `django-concat` | `concat` | `DIFFERENT` |
| `True` | `true` | `SAME` |
| a prose sentence | another prose sentence | `UNRESOLVED` |
| `30 frobnitzes` | `30 seconds` | `UNRESOLVED` (unrecognised unit) |

The last two matter most: the normalizer **declines** rather than guessing, so a
contradiction is never invented from wording it cannot compare.

## 5. Where conflict changes the outcome

A contradiction among otherwise-supported claims produces `EXPOSE_CONFLICTED`,
carrying both values and both source paths. It never silently selects a side,
and it never suppresses the historical claim.
