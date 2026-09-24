"""Deterministic document adapters: PDF (pdfplumber) and DOCX (python-docx).

These are the adapters ADR-0002 promised and Gate 1 declared without emitting.
They follow the same rule as the Python backend: extract what the format
actually states, record what it cannot, and never guess.

WHAT A DOCUMENT BECOMES

A document is an artifact whose lexical units are its pages or paragraphs:

    report.pdf  (document)
      ├─ page 1   CONTAINS, evidence = that page's extracted text
      ├─ page 2
      └─ ...

Nothing else is invented. There are no entities, no topics, no summaries -- a
page is a container, which is exactly what CONTAINS means for code too, so the
whole downstream pipeline (occurrence identity, evidence verification,
retrieval, the answer gate) applies unchanged.

LOCATORS AND WHY VERIFICATION IS *REPRODUCIBLE*

A PDF's file bytes are compressed; they carry no offsets a reader could use.
So a unit's locator records the reader-visible address -- page number, or
paragraph index -- plus offsets into the EXTRACTED TEXT, clearly labelled. The
verifier re-runs the same pinned extractor and compares the text, which is
`REPRODUCIBLE`, not `EXACT`. Calling it EXACT would claim a byte comparison
that never happened.

WHAT IS NOT CLAIMED

* No OCR. A PDF whose pages yield no text is recorded as `PARTIAL` with a
  TEXT_UNAVAILABLE diagnostic per page. It is not silently empty and it is not
  pretended to be readable.
* No page numbers for DOCX. The format does not carry them -- pagination is
  decided by the renderer -- so paragraph and table indices are used instead.
"""
from __future__ import annotations

import io

from kgc.analysis.interface import CodeAnalysis, RawSymbol
from kgc.ir import Diagnostic, Locator, LocatorKind, ParseStatus

PDF_BACKEND_ID = "pdfplumber"
DOCX_BACKEND_ID = "python_docx"


def _versions() -> tuple[str, str]:
    try:
        import pdfplumber
        pdf_v = pdfplumber.__version__
    except Exception:
        pdf_v = "unavailable"
    try:
        import docx                                   # noqa: F401
        from importlib.metadata import version
        docx_v = version("python-docx")
    except Exception:
        docx_v = "unavailable"
    return pdf_v, docx_v


def _unavailable(artifact_id: str, backend: str, what: str) -> CodeAnalysis:
    an = CodeAnalysis(backend_id=backend, backend_version="unavailable",
                      language=what, parse_status=ParseStatus.FAILED)
    an.parse_error = (f"{backend} is not installed; {what} files cannot be read. "
                      f"Install the optional document extras.")
    an.diagnostics.append(Diagnostic(artifact_id, "ERROR", "ADAPTER_UNAVAILABLE",
                                     an.parse_error, None))
    return an


def _document_root(name: str, total: int, kind: LocatorKind, extra: dict) -> RawSymbol:
    return RawSymbol("document", name, name, None,
                     Locator(kind, {**extra, "byte_start": 0, "byte_end": total,
                                    "offsets_index": "extracted_text"}), None)


def analyze_pdf(artifact_id: str, data: bytes, doc_name: str = "document") -> CodeAnalysis:
    """One symbol per page, with that page's extracted text as its evidence."""
    pdf_v, _ = _versions()
    if pdf_v == "unavailable":
        return _unavailable(artifact_id, PDF_BACKEND_ID, "pdf")
    import pdfplumber

    an = CodeAnalysis(backend_id=PDF_BACKEND_ID, backend_version=pdf_v,
                      language="pdf", parse_status=ParseStatus.OK)
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as e:                             # encrypted, truncated, not a PDF
        an.parse_status = ParseStatus.FAILED
        an.parse_error = f"{type(e).__name__}: {e}"
        an.diagnostics.append(Diagnostic(artifact_id, "ERROR", "PDF_UNREADABLE",
                                         an.parse_error, None))
        return an

    units, offset, empty = [], 0, 0
    try:
        with pdf:
            for i, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception as e:
                    text = ""
                    an.diagnostics.append(Diagnostic(
                        artifact_id, "WARNING", "PDF_PAGE_UNREADABLE",
                        f"page {i}: {type(e).__name__}: {e}", None))
                if not text.strip():
                    empty += 1
                    an.diagnostics.append(Diagnostic(
                        artifact_id, "INFO", "TEXT_UNAVAILABLE",
                        f"page {i} yields no extractable text. This is what a scanned "
                        f"page looks like; no OCR is performed and none is claimed.",
                        None))
                    continue
                units.append((i, text, offset, offset + len(text)))
                offset += len(text) + 1
    except Exception as e:
        an.parse_status = ParseStatus.FAILED
        an.parse_error = f"{type(e).__name__}: {e}"
        an.diagnostics.append(Diagnostic(artifact_id, "ERROR", "PDF_UNREADABLE",
                                         an.parse_error, None))
        return an

    an.symbols.append(_document_root(doc_name, max(offset - 1, 0),
                                     LocatorKind.PDF_BOX, {"page": 0}))
    for page_no, text, start, end in units:
        an.symbols.append(RawSymbol(
            "page", f"page {page_no}", f"{doc_name}.page_{page_no}", doc_name,
            Locator(LocatorKind.PDF_BOX, {"page": page_no, "byte_start": start,
                                          "byte_end": end,
                                          "offsets_index": "extracted_text"}),
            text[:400]))
    if empty and not units:
        an.parse_status = ParseStatus.FAILED
        an.parse_error = (f"no page yielded extractable text ({empty} pages). The "
                          f"document may be scanned images; OCR is not performed.")
    elif empty:
        an.parse_status = ParseStatus.PARTIAL
        an.parse_error = f"{empty} of {empty + len(units)} pages yielded no text"
    return an


def analyze_docx(artifact_id: str, data: bytes, doc_name: str = "document") -> CodeAnalysis:
    """One symbol per non-empty paragraph and per table.

    DOCX carries no page numbers -- pagination belongs to whatever renders the
    file -- so the address is the body index, which is stable and real.
    """
    _, docx_v = _versions()
    if docx_v == "unavailable":
        return _unavailable(artifact_id, DOCX_BACKEND_ID, "docx")
    import docx

    an = CodeAnalysis(backend_id=DOCX_BACKEND_ID, backend_version=docx_v,
                      language="docx", parse_status=ParseStatus.OK)
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as e:
        an.parse_status = ParseStatus.FAILED
        an.parse_error = f"{type(e).__name__}: {e}"
        an.diagnostics.append(Diagnostic(artifact_id, "ERROR", "DOCX_UNREADABLE",
                                         an.parse_error, None))
        return an

    units, offset = [], 0
    for i, para in enumerate(document.paragraphs):
        text = (para.text or "").strip()
        if not text:
            continue
        units.append(("paragraph", i, text, offset, offset + len(text)))
        offset += len(text) + 1
    for j, table in enumerate(document.tables):
        rows = ["\t".join(c.text.strip() for c in row.cells) for row in table.rows]
        text = "\n".join(r for r in rows if r.strip())
        if not text:
            continue
        units.append(("table", j, text, offset, offset + len(text)))
        offset += len(text) + 1

    an.symbols.append(_document_root(doc_name, max(offset - 1, 0),
                                     LocatorKind.DOCX_PARA, {"para": -1}))
    for kind, index, text, start, end in units:
        an.symbols.append(RawSymbol(
            kind, f"{kind} {index}", f"{doc_name}.{kind}_{index}", doc_name,
            Locator(LocatorKind.DOCX_PARA, {"para": index, "unit": kind,
                                            "byte_start": start, "byte_end": end,
                                            "offsets_index": "extracted_text"}),
            text[:400]))
    if not units:
        an.parse_status = ParseStatus.PARTIAL
        an.parse_error = "the document contains no extractable paragraph or table text"
        an.diagnostics.append(Diagnostic(artifact_id, "INFO", "TEXT_UNAVAILABLE",
                                         an.parse_error, None))
    return an


def extracted_text(data: bytes, suffix: str) -> dict:
    """Re-derive the unit texts. The evidence verifier's single source of truth.

    Returned as {(unit_key): text}: {page number} for PDF, {(kind, index)} for
    DOCX. Deterministic for a given file and a given library version.
    """
    out: dict = {}
    if suffix == ".pdf":
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                try:
                    out[i] = page.extract_text() or ""
                except Exception:
                    out[i] = ""
    elif suffix == ".docx":
        import docx
        document = docx.Document(io.BytesIO(data))
        for i, para in enumerate(document.paragraphs):
            out[("paragraph", i)] = (para.text or "").strip()
        for j, table in enumerate(document.tables):
            rows = ["\t".join(c.text.strip() for c in row.cells) for row in table.rows]
            out[("table", j)] = "\n".join(r for r in rows if r.strip())
    return out
