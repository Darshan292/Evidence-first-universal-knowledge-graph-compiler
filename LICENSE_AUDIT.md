# LICENSE AUDIT

**Date:** 2026-09-22. Metadata read directly from PyPI / npm registries and
upstream `LICENSE` files on that date.

## 1. Project license

**Recommendation: Apache-2.0.**

Rationale: permissive (matches the permissive dependency set), and unlike MIT it
includes an **express patent grant** and a patent-retaliation clause. For a tool
performing static analysis and graph construction — an area with existing patent
activity — that grant has real value for contributors and adopters. Apache-2.0
is compatible with every required dependency below.

> This is a recommendation, not a decision taken unilaterally. The repository
> owner should add the `LICENSE` file; it is deliberately not created here
> because choosing a project license is the owner's call.

## 2. Required dependencies (core path)

| Package | Version | License | Verified | Notes |
|---|---|---|---|---|
| Python stdlib (`sqlite3`, `ast`, `symtable`, `hashlib`, `json`, `re`) | 3.11+ | PSF-2.0 | yes | Apache-2.0 compatible |
| SQLite (via stdlib) | 3.45.1 | **Public Domain** | yes | FTS5 confirmed compiled in |
| `pdfplumber` | 0.11.10 | MIT | PyPI classifier | PDF text + char/word bbox |
| `pypdfium2` (pdfplumber dep) | 5.13.0 | Apache-2.0 / BSD-3 | yes | PDFium bindings |
| `python-docx` | 1.2.0 | MIT | PyPI metadata | DOCX structure |

**Total required third-party footprint: 3 direct packages.** No compiled ML
runtime, no GPU stack, no server.

## 3. Frontend

| Package | Version | License | Verified |
|---|---|---|---|
| `sigma` | 3.0.3 | MIT | npm registry |
| `graphology` | 0.26.0 | MIT | npm registry |

Both actively maintained (`sigma` modified 2026-09-16). Vendored into the repo so
the UI works offline with no install step.

## 4. Optional extras (explicitly NOT required)

| Package | Version | License | Why optional |
|---|---|---|---|
| `docling` | 2.129.0 | MIT | **resolves to 119 packages** incl. `nvidia-*` CUDA stack, `opencv-python`, torch. Opt-in for scanned/complex-layout PDFs only |
| `ladybug` (LadybugDB) | 0.20.4 | MIT | deferred graph projection (ADR-0004); FTS/vector extensions download at runtime from a vendor CDN |
| `fastembed` | 0.8.0 | Apache-2.0 | deferred dense embeddings; ONNX-based, **no torch** — the reason it is the candidate |
| `tree-sitter` | 0.26.0 | MIT | deferred to language #2 |
| `tree-sitter-python` | 0.25.0 | MIT | deferred |

## 5. Rejected on license grounds

| Package | License | Verdict |
|---|---|---|
| **PyMuPDF / fitz** | **AGPL-3.0** (or paid commercial) | **Rejected.** Despite being materially faster at text extraction, AGPL would impose copyleft on downstream users deploying this as a hosted or internal shared tool. Incompatible with the permissive posture. `pdfplumber` chosen instead — and it also gives char-level bboxes, which is *better* evidence anchoring. |

This is the single most important row in the audit: PyMuPDF is the default PDF
choice in most RAG tutorials, and adopting it by reflex would silently make the
project AGPL-encumbered.

## 6. Model licenses

Model weights carry **separate** licenses from the code that runs them. Ollama is
Apache-2.0-ish tooling, but the models it serves are not uniformly permissive
(Llama community licenses, Gemma terms, etc. carry use restrictions).

**Policy:** the project ships **no model weights** and pins no specific model as
a hard default in code. Documentation names candidate models and directs users to
verify the license of any model they choose. A model license is the user's
compliance obligation, and we will say so plainly in the README rather than
pretending the question does not exist.

## 7. Copyleft gate (acceptance test A-20)

CI fails if any package in the **required** dependency set resolves to GPL, LGPL,
AGPL, SSPL, BUSL or any source-available non-OSI license. Optional extras are
scanned and reported but do not fail the build, since a user opting into an extra
accepts its terms.

## 8. Re-audit triggers

- Any new required dependency.
- Any dependency major-version bump.
- Before any tagged release.
- Quarterly for the optional set.
