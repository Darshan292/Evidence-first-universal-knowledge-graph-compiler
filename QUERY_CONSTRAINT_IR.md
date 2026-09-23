# QUERY CONSTRAINT IR

**Status:** Implemented in `experiments/retrieval/constraints.py`. **Date:** 2026-09-23

## 1. The contract

```
QueryConstraints
├── subject          str | None
├── subjects         list[str]        all candidate subjects named
├── predicate        canonical predicate | None
├── prop_word        the natural-language property word, unmapped
├── obj              str | None
├── literal          list[str]        numbers and quoted strings
├── operator         str | None
├── source_scope     file name | None
├── temporal_scope   str | None
└── parse_status     PARSED | PARTIAL | UNPARSEABLE | AMBIGUOUS
```

## 2. `parse_status` is the whole point

| Status | Meaning | Support layer |
|---|---|---|
| `PARSED` | every field the query shape needs is present | proceed |
| `PARTIAL` | a field is recognised but incomplete | **ABSTAIN** |
| `UNPARSEABLE` | no rule applies | **ABSTAIN** |
| `AMBIGUOUS` | more than one reading applies | **ABSTAIN_AMBIGUOUS** |

> **A partially understood query must never silently become a different query.**
>
> *"what port does TimestampSigner listen on"* once returned the class's
> definition, because an unmatched property fell back to bare-identifier lookup.
> It now yields `PARTIAL` / `interrogative_unmapped` and abstains. This is
> permanently covered by `test_port_question_never_degrades_to_definition`.

## 3. Recognised shapes

| Shape | Example | Yields |
|---|---|---|
| `identifier` | `SignatureExpired` | subject |
| `property` | "what is the default salt of Serializer" | subject + prop_word (+ predicate if mapped) |
| `relation` | "which class does TimestampSigner extend" | subject + predicate + object |
| `interrogative_unmapped` | "what port does X listen on" | **PARTIAL** — refuses |
| `unparsed` | "why should different salts be used" | **UNPARSEABLE** — refuses |

An interrogative that names entities but yields neither a property nor a
relation is `PARTIAL`, never `identifier`. That single rule is what stopped the
degradation bug.

## 4. Coverage — and an honest caveat about the number

| Query set | n | Parse rate |
|---|---|---|
| Gate 2.25 template-generated (corpus3) | 62 | **0.984** |
| **Gate 2 hand-written (corpus2)** | 20 | **0.500** |

**The 0.984 is not the parser's general coverage.** I wrote both the query
templates and the parser, so the templates emit shapes the parser handles. The
hand-written set — written before this parser existed — scores **0.50**.

**The honest figure for unseen phrasing is ~0.50, not 0.98**, and the scaled
availability numbers inherit that inflation.

Note also that hand-written coverage *fell* from 0.70 (Gate 2 parser) to 0.50:
the stricter IR refuses queries the old one silently mis-answered. That is the
safety/availability trade made visible, and it is the right direction.

## 5. What does not belong here

No LLM, no learned model, no synonym table, no query-specific exception, no
corpus-specific special case. When a rule does not apply, the answer is a status
value — not a guess.
