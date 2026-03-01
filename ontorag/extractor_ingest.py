# extractor_ingest.py
"""
Document ingestion with two selectable engines:

  - **llamaindex** (default) — traditional fixed-size chunking via
    LlamaIndex SimpleDirectoryReader + SentenceSplitter.  Broad format
    support out of the box.

  - **pageindex** — reasoning-based hierarchical section detection via
    the PageIndex hosted API.  Produces a tree of natural document
    sections (PDF), flattened into ChunkDTOs with section path
    provenance.  Requires a PAGEINDEX_API_KEY.

Both engines produce the same DocumentDTO / ChunkDTO output.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ontorag.dto import (
    DocumentDTO, ChunkDTO, ProvenanceDTO,
    stable_document_id, stable_chunk_id, hash_text, hash_file,
)
from ontorag.verbosity import get_logger

_log = get_logger("ontorag.extractor_ingest")

# Formats that PageIndex API handles natively (PDF only; MD uses local splitter)
_PAGEINDEX_EXTS = {".pdf"}
_MARKDOWN_EXTS = {".md", ".markdown"}


def clean_snippet(text: str, max_len: int = 240) -> str:
    t = " ".join(text.split())
    return (t[:max_len] + "…") if len(t) > max_len else t


# =====================================================================
#  PageIndex engine
# =====================================================================

def _flatten_tree(
    nodes: List[Dict[str, Any]],
    pages: List[str],
    path: List[str],
) -> List[Dict[str, Any]]:
    """Recursively flatten a PageIndex tree into leaf chunks."""
    results: List[Dict[str, Any]] = []
    for node in nodes:
        title = node.get("title", "")
        current_path = path + [title] if title else path
        children = node.get("nodes", [])

        if children:
            results.extend(_flatten_tree(children, pages, current_path))
        else:
            start = node.get("start_index", 0)
            end = node.get("end_index", start)
            text = node.get("text") or "\n".join(pages[start:end + 1])
            if not text.strip():
                continue
            results.append({
                "text": text,
                "section": " > ".join(current_path),
                "start_page": start,
                "end_page": end,
                "title": title,
            })
    return results


def _get_pageindex_client():
    """Return a PageIndexClient using PAGEINDEX_API_KEY from env."""
    import os
    from pageindex import PageIndexClient

    api_key = os.environ.get("PAGEINDEX_API_KEY")
    if not api_key:
        raise RuntimeError(
            "PAGEINDEX_API_KEY is not set. "
            "Get an API key at https://pageindex.ai and add it to your .env"
        )
    return PageIndexClient(api_key=api_key)


def _poll_until_ready(client, doc_id: str, timeout: int = 300, interval: int = 3) -> None:
    """Poll document status until processing completes or times out."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        meta = client.get_document(doc_id)
        status = meta.get("status", "")
        if status == "completed":
            return
        if status == "failed":
            raise RuntimeError(f"PageIndex processing failed for doc_id={doc_id}")
        _log.debug("  doc %s status=%s, polling…", doc_id, status)
        time.sleep(interval)
    raise TimeoutError(f"PageIndex processing timed out after {timeout}s for doc_id={doc_id}")


def _run_pageindex(file_path: str) -> tuple[Optional[str], List[Dict[str, Any]], List[str]]:
    """Submit a PDF to the PageIndex API and retrieve tree + OCR.

    Returns (doc_title, flat_chunks, pages).
    """
    client = _get_pageindex_client()

    _log.info("Submitting %s to PageIndex API…", file_path)
    resp = client.submit_document(file_path)
    doc_id = resp["doc_id"]
    _log.info("  doc_id=%s — waiting for processing…", doc_id)

    _poll_until_ready(client, doc_id)

    tree_resp = client.get_tree(doc_id)
    ocr_resp = client.get_ocr(doc_id, format="page")

    doc_title = tree_resp.get("doc_name")
    structure = tree_resp.get("structure", [])
    pages = [p.get("text", "") for p in ocr_resp.get("pages", [])]

    flat = _flatten_tree(structure, pages, [])
    return doc_title, flat, pages


# ── Local Markdown section-aware chunker ─────────────────────────────

import re
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)", re.MULTILINE)


def _run_markdown_splitter(file_path: str) -> tuple[Optional[str], List[Dict[str, Any]], List[str]]:
    """Split a Markdown file into chunks along heading boundaries.

    Returns (doc_title, flat_chunks, pages) where pages is the raw lines.
    """
    md_text = Path(file_path).read_text(encoding="utf-8")
    lines = md_text.split("\n")

    # Build list of (level, title, start_line)
    headings: list[tuple[int, str, int]] = []
    for m in _HEADING_RE.finditer(md_text):
        level = len(m.group(1))
        title = m.group(2).strip()
        line_no = md_text[:m.start()].count("\n")
        headings.append((level, title, line_no))

    if not headings:
        # No headings — return entire file as one chunk
        return None, [{"text": md_text, "section": "", "start_page": 0, "end_page": len(lines) - 1, "title": ""}], lines

    doc_title = headings[0][1] if headings[0][0] == 1 else None

    # Build section spans
    flat: List[Dict[str, Any]] = []
    path_stack: list[tuple[int, str]] = []

    for idx, (level, title, start_line) in enumerate(headings):
        end_line = headings[idx + 1][2] - 1 if idx + 1 < len(headings) else len(lines) - 1

        # Maintain a breadcrumb stack
        while path_stack and path_stack[-1][0] >= level:
            path_stack.pop()
        path_stack.append((level, title))
        section = " > ".join(t for _, t in path_stack)

        text = "\n".join(lines[start_line:end_line + 1]).strip()
        if not text:
            continue
        flat.append({
            "text": text,
            "section": section,
            "start_page": start_line,
            "end_page": end_line,
            "title": title,
        })

    return doc_title, flat, lines


# ── Fallback text extraction (used by PageIndex for non-PDF/MD) ──────

def _extract_text_fallback(file_path: str) -> tuple[str, Optional[str]]:
    """Extract raw text from non-PDF/Markdown files."""
    ext = Path(file_path).suffix.lower()

    if ext == ".epub":
        import ebooklib
        from ebooklib import epub
        import html2text

        book = epub.read_epub(file_path)
        h = html2text.HTML2Text()
        h.ignore_links = False
        parts = []
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            parts.append(h.handle(item.get_content().decode("utf-8", errors="replace")))
        return "\n\n".join(parts), "application/epub+zip"

    try:
        import fitz  # PyMuPDF
        doc = fitz.open(file_path)
        pages = [page.get_text() for page in doc]
        doc.close()
        return "\n\n".join(pages), None
    except Exception:
        pass

    return Path(file_path).read_text(encoding="utf-8", errors="replace"), None


def _chunk_text(text: str, chunk_size: int = 3000, overlap: int = 200) -> List[str]:
    """Split text into overlapping chunks by character count."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def extract_with_pageindex(file_path: str, mime: Optional[str] = None) -> DocumentDTO:
    """Ingest using PageIndex (hierarchical) + fallback for non-PDF/MD."""
    content_hash = hash_file(file_path)
    doc_id = stable_document_id(file_path)
    ext = Path(file_path).suffix.lower()

    _log.info("Ingesting %s [engine=pageindex] (doc_id=%s, hash=%s)", file_path, doc_id, content_hash[:12])

    out = DocumentDTO(
        document_id=doc_id, source_path=file_path,
        source_mime=mime, content_hash=content_hash, title=None, chunks=[],
    )

    if ext in _MARKDOWN_EXTS:
        _log.info("Using local Markdown splitter for %s", ext)
        doc_title, flat_chunks, _pages = _run_markdown_splitter(file_path)
        out.title = doc_title

        for i, fc in enumerate(flat_chunks):
            text = fc["text"]
            section = fc.get("section")
            start_page = fc.get("start_page")
            prov = ProvenanceDTO(
                source_path=file_path, source_mime=mime,
                page=start_page,
                page_label=str(start_page) if start_page is not None else None,
                section=section, text_snippet=clean_snippet(text), raw=fc,
            )
            chunk = ChunkDTO(
                document_id=doc_id,
                chunk_id=stable_chunk_id(doc_id, i, start_page),
                chunk_index=i, text=text, provenance=prov,
                text_hash=hash_text(text),
            )
            out.chunks.append(chunk)
            _log.debug("  chunk %d: id=%s len=%d section=%s", i, chunk.chunk_id, len(text), section)

    elif ext in _PAGEINDEX_EXTS:
        _log.info("Using PageIndex API for %s", ext)
        doc_title, flat_chunks, _pages = _run_pageindex(file_path)
        out.title = doc_title

        for i, fc in enumerate(flat_chunks):
            text = fc["text"]
            section = fc.get("section")
            start_page = fc.get("start_page")
            prov = ProvenanceDTO(
                source_path=file_path, source_mime=mime,
                page=start_page,
                page_label=str(start_page) if start_page is not None else None,
                section=section, text_snippet=clean_snippet(text), raw=fc,
            )
            chunk = ChunkDTO(
                document_id=doc_id,
                chunk_id=stable_chunk_id(doc_id, i, start_page),
                chunk_index=i, text=text, provenance=prov,
                text_hash=hash_text(text),
            )
            out.chunks.append(chunk)
            _log.debug("  chunk %d: id=%s len=%d section=%s", i, chunk.chunk_id, len(text), section)
    else:
        _log.info("Fallback text extraction for %s", ext)
        full_text, detected_mime = _extract_text_fallback(file_path)
        if detected_mime and not mime:
            out.source_mime = detected_mime
        for i, text in enumerate(_chunk_text(full_text)):
            if not text.strip():
                continue
            prov = ProvenanceDTO(
                source_path=file_path, source_mime=mime or detected_mime,
                text_snippet=clean_snippet(text),
            )
            chunk = ChunkDTO(
                document_id=doc_id,
                chunk_id=stable_chunk_id(doc_id, i, None),
                chunk_index=i, text=text, provenance=prov,
                text_hash=hash_text(text),
            )
            out.chunks.append(chunk)

    _log.info("Created DocumentDTO with %d chunks", len(out.chunks))
    return out


# =====================================================================
#  LlamaIndex engine
# =====================================================================

def extract_with_llamaindex(file_path: str, mime: Optional[str] = None) -> DocumentDTO:
    """Ingest using LlamaIndex SimpleDirectoryReader + SentenceSplitter."""
    from llama_index.core import SimpleDirectoryReader
    from llama_index.core.node_parser import SentenceSplitter

    content_hash = hash_file(file_path)
    doc_id = stable_document_id(file_path)

    _log.info("Ingesting %s [engine=llamaindex] (doc_id=%s, hash=%s)", file_path, doc_id, content_hash[:12])

    docs = SimpleDirectoryReader(input_files=[file_path]).load_data()
    _log.debug("LlamaIndex loaded %d raw documents", len(docs))

    splitter = SentenceSplitter(chunk_size=1024, chunk_overlap=120)
    nodes = splitter.get_nodes_from_documents(docs)
    _log.info("Split into %d chunks (chunk_size=1024, overlap=120)", len(nodes))

    out = DocumentDTO(
        document_id=doc_id, source_path=file_path,
        source_mime=mime, content_hash=content_hash, title=None, chunks=[],
    )

    for i, node in enumerate(nodes):
        text = node.get_content() if hasattr(node, "get_content") else str(node.text)
        meta = {}
        if hasattr(node, "metadata") and isinstance(node.metadata, dict):
            meta = node.metadata

        page = meta.get("page") or meta.get("page_number")
        page_label = meta.get("page_label")
        section = meta.get("section") or meta.get("header")

        prov = ProvenanceDTO(
            source_path=file_path, source_mime=mime,
            page=int(page) if page is not None and str(page).isdigit() else None,
            page_label=str(page_label) if page_label is not None else None,
            section=str(section) if section is not None else None,
            offset_start=meta.get("offset_start"),
            offset_end=meta.get("offset_end"),
            text_snippet=clean_snippet(text), raw=meta,
        )
        chunk = ChunkDTO(
            document_id=doc_id,
            chunk_id=stable_chunk_id(doc_id, i, prov.page),
            chunk_index=i, text=text, provenance=prov,
            text_hash=hash_text(text),
        )
        out.chunks.append(chunk)
        _log.debug("  chunk %d: id=%s len=%d page=%s", i, chunk.chunk_id, len(text), prov.page)

    _log.info("Created DocumentDTO with %d chunks", len(out.chunks))
    return out


# =====================================================================
#  Unified dispatcher
# =====================================================================

ENGINES = {"pageindex": extract_with_pageindex, "llamaindex": extract_with_llamaindex}


def extract_document(
    file_path: str,
    mime: Optional[str] = None,
    engine: str = "llamaindex",
) -> DocumentDTO:
    """Ingest a document using the selected engine.

    Args:
        file_path: Path to the input file.
        mime: Optional MIME type override.
        engine: ``"llamaindex"`` (default) or ``"pageindex"``.
    """
    fn = ENGINES.get(engine)
    if fn is None:
        raise ValueError(f"Unknown engine {engine!r}. Choose from: {', '.join(ENGINES)}")
    return fn(file_path, mime=mime)
