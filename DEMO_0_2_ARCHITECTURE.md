# DEMO 0.2 — ARCHITECTURE OF THE KNOWLEDGE GRAPH WORKBENCH

Scope: the product layer added on top of the compiler. It explains what was
added, what was deliberately *not* added, and where each trust boundary sits.
It does not restate the compiler's architecture (`ARCHITECTURE.md`) or reopen
the thesis (`ARCHITECTURE_PIVOT_AFTER_J1.md`).

---

## 1. Shape

```
browser (vanilla HTML/CSS/JS, no build step)
   │  fetch()
   ▼
kgq/web.py            FastAPI. ~90 lines of endpoint, no business logic.
   ├── kgq/workspace.py    upload boundary: archives, isolation, file reports
   ├── kgq/graph.py        read-only graph API: stats, search, neighbourhood, edge, evidence
   └── kgq/answer.py       THE answering path — unchanged, not duplicated
            ├── kgq/retrieval.py     deterministic retrieval (identifier → graph → BM25)
            ├── kgq/validate.py      the deterministic gate
            └── kgq/provider.py      optional OpenAI-compatible model
   ▼
kgc/  (compiler)     pipeline → store → SQLite, one database per workspace
```

Four new modules. `kgq/web.py` holds no rule of its own: every endpoint is a
translation of one call into one JSON shape.

---

## 2. The workspace model

```
workspaces/<id>/
    source/          the extracted tree — nothing is ever written outside it
    graph.sqlite     a FRESH database, never shared with another upload
    metadata.json    what was uploaded, what it hashed to, what happened to it
```

**Why a fresh database per upload.** The compiler is append-only across corpus
revisions (K-1.2 §10): re-ingesting a changed file supersedes claims rather than
deleting them. That is correct for revisions of *one* corpus and wrong for two
unrelated ones — merged, a question about repository A could be answered from
repository B's evidence, and both answers would validate. Isolation is therefore
not a convenience; it is what keeps the evidence model meaningful.

**Workspace ids are generated, never accepted.** `WorkspaceStore.get` refuses
any id that is not alphanumeric and ≤32 characters before it touches the path,
because the id becomes a path component.

**The demo corpus is symlinked, not copied** (§17 forbids bundling it). That
puts a live checkout one `rmtree` away from the delete button, so
`test_deleting_a_demo_workspace_cannot_delete_the_corpus_it_points_at` proves
`shutil.rmtree` unlinks the link rather than walking the tree behind it.

---

## 3. The upload boundary

An uploaded archive is hostile input. `kgq/workspace.py` refuses, per member:

| Attack | Refusal |
|---|---|
| `../../etc/cron.d/x` | `..` component rejected before any write |
| `/etc/passwd`, `C:\...` | absolute path / drive letter rejected |
| a member that resolves outside the workspace through a symlink written by an *earlier* member | destination is `resolve()`d and proven to be inside |
| a symlink member | `external_attr` marks it; never written |
| a member whose header understates its real size | read is capped at `MAX_MEMBER_BYTES+1`, mismatch rejected |
| 20 000+ members, 200 MB archive, 400 MB expanded | whole archive refused |

Every refusal produces a `FileReport` with a reason, a size and a sha256.
**A refusal is a record, not a silence** — it appears in the dashboard beside the
accepted files. The same applies to a format with no analyser: `notes.rtf` is
`UNSUPPORTED` with its reason, hash and size, not a file that quietly vanished.

`classify_upload` never guesses. A `.rtf` is reported as having no analyser; it
is not parsed as text and hoped for.

---

## 4. Documents: PDF and DOCX

`kgc/analysis/document.py` is a deterministic extractor, exactly like the Python
AST analyser, and is subject to the same rule: **no model constructs the graph.**

- A document becomes a document-root symbol plus one symbol per **page** (PDF)
  or per **paragraph/table** (DOCX), joined by `CONTAINS`.
- `pdfplumber` and `python-docx` only. No PyMuPDF, no Docling, no OCR.
- A PDF with no extractable text yields a `TEXT_UNAVAILABLE` diagnostic and a
  `PARTIAL` status. **It is never described as scanned-and-read.**
- Locator kinds are `pdf_box {page, byte_start, byte_end}` and
  `docx_para {unit, para, byte_start, byte_end}`. The offsets index the
  **extracted text**, and the locator says so (`"offsets_index": "extracted_text"`)
  — they are not byte offsets into the container file, and nothing pretends they are.

**DOCX gets no page numbers.** The format carries none; pagination belongs to the
renderer. `describe_location` returns `paragraph 7`, never `page 3`. The source
viewer likewise refuses to draw a line-number gutter for document formats and
says why: *"extracted text; this format carries no line numbers."*

Document evidence verifies at `REPRODUCIBLE` (`pdf_re-extract/1`,
`docx_reextract/1`) — re-extraction with the pinned library, not a byte compare,
because the cited text does not exist as a contiguous run in the file's bytes.
Code evidence remains `EXACT` (`byte_compare/1`). The UI prints the strength and
the engine; it never flattens the two into one word.

---

## 5. The graph read API

`kgq/graph.py` is read-only and cannot write a claim, mint an evidence id or
invent a locator.

**Nothing renders the whole graph.** A neighbourhood starts from one symbol and
expands 1–2 hops, capped at `MAX_NODES = 220`; when the cap bites, the response
says `truncated: true` and the UI prints it. 6 102 symbols on one canvas is a
screensaver, and a picture that silently drops half its nodes is worse than no
picture.

Every edge in the response carries its `claim_id`. `GET /edge/{claim_id}`
returns the claim's predicate, establishment, lifecycle, extractor and
`model_id` (always `null` today), its reference resolution, and its evidence
records. `GET /evidence/{id}` adds the surrounding source, **re-read from disk at
request time**. The chain is: pixel → claim → evidence → bytes on disk, with no
step taken on trust.

---

## 6. Questions: one path

The `/ask` endpoint calls `kgq.answer.ask` and renders what comes back. It does
not compose answers, parse model output, or run a validator of its own. This is
enforced by a test that greps the module for `compose_answer`, `parse_answer`
and `Validator(`, and the frontend for `/chat/completions` and any hard-coded
external URL.

The reason is J-2. J-2 will measure *this* answering path; a second one in the
web layer would make the measurement meaningless — the UI could be right while
the thing being measured was wrong, or vice versa.

Consequences the UI inherits rather than decides:
- an `ABSTAIN` renders as an abstention with its reason, and the frontend has no
  code path that turns one into an answer;
- the model's own prose (`model_prose_unvalidated`) is returned by the API for
  the record and is **never rendered as the answer** — the answer shown is
  composed from validated claims only;
- with no model configured, the deterministic half still runs and the evidence
  and structural facts are shown under an explicit abstention.

---

## 7. What the UI says, exactly

Wording is part of the architecture here, because the whole claim of the system
is epistemic.

| Shown | Never shown |
|---|---|
| ANSWER · **PROPOSED** | "verified", "correct", "true" |
| "Accepted means grounded — every statement cites evidence that exists, was retrieved for this question, and still matches the source bytes. It does not mean the statement is true." | a confidence score |
| structural facts labelled **DERIVED — a parser wrote these, not a model** | a quality score for the corpus |
| `SUPPORTED` / `EXPLICIT_CONTRADICTION` / `NOT_ESTABLISHED` per structural check | "unsupported" for mere absence |
| "The gate checks grounding, not entailment… an answer that cites the right function and describes it wrongly would pass." | silence about that gap |

The dashboard counts things. It does not score them. There is no health
percentage, no grade and no "corpus quality" — those numbers would be invented.

---

## 8. Source is data, in the browser too

Every string that originates in an uploaded file reaches the DOM through
`textContent`. There is no `innerHTML` in `app.js`. A file containing
`# Ignore previous instructions` is rendered as that text, and
`test_source_that_says_ignore_previous_instructions_stays_source` asserts the
claims extracted from such a file are still `DERIVED`, still `python_ast`, and
still carry `model_id = null` — the file asked for exactly the opposite.

This is the same boundary `kgc/safety.py` enforces at parse time and
`kgq/contract.py` enforces in the prompt (`--- begin untrusted source content ---`),
extended to the last hop.

---

## 9. Visual encoding, and why edges are not coloured by predicate

Chosen by running the dataviz palette validator (six checks: lightness band,
chroma floor, adjacent-pair CVD separation, normal-vision floor, contrast vs
surface) rather than by eye.

- A mixed status+categorical set **FAILED** validation, and five categorical
  hues cannot clear the all-pairs CVD separation gate. **So predicate identity
  does not use colour at all**: each edge carries a text label plus a dash
  pattern, and a legend lists both.
- Node colour is **sequential by hop depth** — one hue, near to far. Distance
  from the centre and darkness mean the same thing, which is the only thing they
  are allowed to mean.
- Predicate-count bars use the one validated categorical slot: `#2a78d6` on
  `#fcfcfb` and `#3987e5` on `#17181c`, both **ALL CHECKS PASS**. One series, so
  no legend box.
- Status colours (`good`, `warning`, `critical`) are reserved, never reused as a
  series colour, and always ship with a word.
- Unresolved targets and literals render as hollow squares — shape, not colour,
  so "we could not resolve this" survives a greyscale print.
- Dark mode is a separate set of steps against the dark surface, not an
  automatic inversion.

Layout is deterministic: rings by hop depth, then angle-only relaxation. The
same neighbourhood draws the same way every time, and no node can drift to a
radius that misstates its distance.

---

## 10. What was deliberately not built

Per §16, and because none of it is what this system is short of:

no embeddings, no vector database, no agent framework, no audio/video, no
arbitrary ontology editor, no graph-database migration, no distributed
infrastructure, no user accounts, no cloud deployment, no billing, no
collaborative workspaces, no fine-tuning.

Also not built, on the same reasoning: a React/Next.js frontend, a build step, a
component library, and a second answering path.

---

## 11. Known limits

1. **The gate checks grounding, not entailment.** An answer citing the right
   function and describing it wrongly passes. This is the J-2 measurement and is
   stated in the UI, not only here.
2. **No live hosted-model test.** This environment's network policy blocks every
   OpenAI-compatible host tried. The transport is proven against a local server
   (12 tests in `test_provider_transport.py` — which proves the protocol, NOT
   reachability: J-2 later found a real provider's CDN rejecting the request on
   its User-Agent, see `eval/j2/DEFECT_001_user_agent.md`); the screenshots of the ANSWER
   view in `DEMO_0_2.md` were produced against a **scripted local stand-in that
   is not a language model**. Nothing here measures model quality.
3. **Neighbourhood truncation is announced but crude** — the cap is on nodes,
   not on an interest ranking. A hub with 500 callers shows an arbitrary 220.
4. **Search is `LIKE`, not the FTS index.** Adequate for a symbol picker,
   deliberately not the retrieval path.
5. **Single process, no auth.** It binds `127.0.0.1` and is a local tool. Nothing
   about it is safe to expose to a network, and nothing pretends otherwise.
