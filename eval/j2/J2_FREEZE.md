# J-2 QUERY SET — FROZEN

    file    eval/j2/J2_QUERIES.json
    sha256  977ca0761b769c8797c471b035ad735022295bcf2175446fd6bd843a56b1a713
    count   48   (J2-001 … J2-048, no gaps, ids unique)
    frozen  2026-09-24, before gold labelling and before any system run

The file carries `id` and `question` and nothing else — no categories, no
difficulty labels, no expected answers, no answerability hints.

## Independence — verified first-hand, not taken on the author's word

The authoring agent's own tool-call log was read directly. 45 tool calls, every
file read under `eval/corpus3/`: the Werkzeug source tree, its `docs/`, README,
`pyproject.toml` and `CHANGES.rst`.

Nothing under `kgc/`, `kgq/`, the repo-root `tests/`, `bench/`, `experiments/`,
`ADR/`, any root-level report, or any previous gold or query file was opened.

**One leak, recorded rather than glossed:** a `find eval/` printed the
*filenames* in `eval/`, which include `J1_GOLD.json` and
`J1_INDEPENDENT_QUERIES.json`. No content was read. The author learned that
prior evaluation artefacts exist; it learned nothing about what is in them.

I did not commission this authoring run and did not see the questions before
they were written.

## The validity threat: J-1 is burned, and overlap is unavoidable

J-1 is burned because the **system** was developed with sight of its failures —
the Demo 0.1 retrieval budget fix came directly from J1-006, and the flagship
demo question is the subject of J1-009 / J1-047. Any J-2 question that asks a
fact a J-1 question already asked is a weaker test than a fresh one.

Two independent readers of the same 225-file library converge on the same
interesting behaviours. This is not a defect in the author's work and it is not
evidence of leakage — it is what a finite corpus does.

**No question was removed or edited.** Deleting questions after reading them is
exactly the tuning §18 forbids, and it would destroy the independence that makes
the set worth having. Instead the set is **stratified**, registered *before* gold
exists, so results can be reported separately — the same treatment §15 gives the
demo target questions.

| Stratum | n | Meaning |
|---|---|---|
| `FRESH` | **24** | no J-1 counterpart — **the primary J-2 denominator** |
| `J1_OVERLAP_PARTIAL` | 14 | adjacent fact, different angle — reported separately |
| `J1_OVERLAP_STRONG` | 10 | same underlying fact as a named J-1 question — reported separately |

Strong overlaps, with their J-1 counterparts:

| J-2 | J-1 | Shared fact |
|---|---|---|
| J2-001 | J1-001 | default password hash algorithm |
| J2-002 | J1-009, J1-047 | what `safe_join` rejects — **also the Demo 0.1/0.2 flagship subject** |
| J2-007 | J1-005 | how rule precedence is decided |
| J2-008 | J1-011, J1-015 | `part_isolating` / a converter matching slashes |
| J2-016 | J1-007 | `max_content_length` vs `max_form_memory_size` vs `max_form_parts` |
| J2-017 | J1-014 | when an upload spills to a temp file |
| J2-023 | J1-012 | where the debugger PIN comes from |
| J2-026 | J1-023 | which reloader backend is the default |
| J2-038 | J1-022 | `max_cookie_size` and what happens past it |
| J2-044 | J1-018 | test-client cookie persistence |

Full per-question stratification: `eval/j2/J2_QUERY_PROVENANCE.json`.

**A 24-question primary denominator is below the 36–50 the brief asks for.** That
is a real cost of J-1 being burned, and it is stated here rather than discovered
after the numbers land. The 48-question set still runs in full; what changes is
which subset carries the headline claim.

## What has NOT been done

- **No gold labels.** (§4)
- **No system run, no retrieval, no adjudication, no metric.** (§6–§21)

Deliberately: §4 requires gold to be established from the source without system
output influencing it. Running retrieval over these 48 questions now — even the
model-free half — would put the system's evidence selection in front of whoever
writes the gold next. That is the contamination §4 exists to prevent, so the
diagnostic was not run.

## Still blocked

J-2 cannot run. Every OpenAI-compatible model host, every open-weight host and
the Ollama registry are refused by this environment's egress proxy at CONNECT
with 403. See `eval/j2/J2_BLOCKED.md`. A frozen query set does not change that.
