# extractor_ingest.py
"""
Document ingestion using PageIndex (PDF/Markdown) with fallback
text extraction for other formats (DOCX, HTML, CSV, EPUB, …).

PageIndex produces a *hierarchical tree* of document sections.  We
flatten the leaf nodes into ChunkDTOs while preserving the section
path as provenance.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from ontorag.dto import (
    DocumentDTO, ChunkDTO, ProvenanceDTO,
    stable_document_id, stable_chunk_id, hash_text, hash_file,
)
from ontorag.verbosity import get_logger

_log = get_logger("ontorag.extractor_ingest")

# Formats that PageIndex handles natively
_PAGEINDEX_EXTS = {".pdf", ".md", ".markdown"}


def clean_snippet(text: str, max_len: int = 240) -> str:
    t = " ".join(text.split())
    return (t[:max_len] + "…") if len(t) > max_len else t


# ── PageIndex helpers ────────────────────────────────────────────────

def _flatten_tree(
    nodes: List[Dict[str, Any]],
    pages: List[str],
    path: List[str],
) -> List[Dict[str, Any]]:
    """Recursively flatten a PageIndex tree into leaf chunks.

    Each leaf gets the full section path and its text content.
    """
    results: List[Dict[str, Any]] = []
    for node in nodes:
        title = node.get("title", "")
        current_path = path + [title] if title else path
        children = node.get("nodes", [])

        if children:
            results.extend(_flatten_tree(children, pages, current_path))
        else:
            # Leaf node — extract text from page range
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


def _extract_with_pageindex(file_path: str) -> tuple[Optional[str], List[Dict[str, Any]], List[str]]:
    """Run PageIndex on a PDF or Markdown file.

    Returns (doc_title, flat_chunks, pages).
    """
    ext = Path(file_path).suffix.lower()

    if ext in {".md", ".markdown"}:
        import asyncio
        from pageindex import md_to_tree

        md_text = Path(file_path).read_text(encoding="utf-8")
        tree = asyncio.run(md_to_tree(
            md_path=file_path,
            if_add_node_text="yes",
            if_add_node_id="yes",
            if_add_node_summary="no",
        ))
        pages = md_text.split("\n")
        doc_title = tree.get("title")
        structure = tree.get("structure", tree.get("nodes", []))
        flat = _flatten_tree(structure, pages, [])
        return doc_title, flat, pages

    # PDF path
    from pageindex import page_index
    from pageindex.utils import get_page_tokens

    result = page_index(
        doc=file_path,
        if_add_node_text="yes",
        if_add_node_id="yes",
        if_add_node_summary="no",
    )

    doc_title = result.get("doc_name")
    structure = result.get("structure", [])

    # Get raw page texts for fallback text extraction on leaf nodes
    page_tuples = get_page_tokens(file_path)
    pages = [pt[0] for pt in page_tuples]

    flat = _flatten_tree(structure, pages, [])
    return doc_title, flat, pages


# ── Fallback text extraction ────────────────────────────────────────

def _extract_text_fallback(file_path: str) -> tuple[str, Optional[str]]:
    """Extract raw text from non-PDF/Markdown files.

    Uses PyMuPDF for supported formats, ebooklib for EPUB,
    and falls back to reading as plain text.
    """
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

    # Try PyMuPDF for DOCX, HTML, etc.
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(file_path)
        pages = [page.get_text() for page in doc]
        doc.close()
        return "\n\n".join(pages), None
    except Exception:
        pass

    # Final fallback: read as plain text
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


# ── Public API ───────────────────────────────────────────────────────

def extract_with_pageindex(file_path: str, mime: Optional[str] = None) -> DocumentDTO:
    """Ingest a file and return a DocumentDTO with ChunkDTOs.

    Uses PageIndex for PDFs and Markdown (hierarchical tree → flat
    chunks with section provenance).  Falls back to text extraction
    for other formats.
    """
    content_hash = hash_file(file_path)
    doc_id = stable_document_id(file_path)
    ext = Path(file_path).suffix.lower()

    _log.info("Ingesting %s (doc_id=%s, content_hash=%s)", file_path, doc_id, content_hash[:12])

    out = DocumentDTO(
        document_id=doc_id,
        source_path=file_path,
        source_mime=mime,
        content_hash=content_hash,
        title=None,
        chunks=[],
    )

    if ext in _PAGEINDEX_EXTS:
        _log.info("Using PageIndex for %s", ext)
        doc_title, flat_chunks, _pages = _extract_with_pageindex(file_path)
        out.title = doc_title

        for i, fc in enumerate(flat_chunks):
            text = fc["text"]
            section = fc.get("section")
            start_page = fc.get("start_page")

            prov = ProvenanceDTO(
                source_path=file_path,
                source_mime=mime,
                page=start_page,
                page_label=str(start_page) if start_page is not None else None,
                section=section,
                text_snippet=clean_snippet(text),
                raw=fc,
            )
            chunk = ChunkDTO(
                document_id=doc_id,
                chunk_id=stable_chunk_id(doc_id, i, start_page),
                chunk_index=i,
                text=text,
                provenance=prov,
                text_hash=hash_text(text),
            )
            out.chunks.append(chunk)
            _log.debug("  chunk %d: id=%s len=%d section=%s", i, chunk.chunk_id, len(text), section)
    else:
        _log.info("Fallback text extraction for %s", ext)
        full_text, detected_mime = _extract_text_fallback(file_path)
        if detected_mime and not mime:
            out.source_mime = detected_mime

        raw_chunks = _chunk_text(full_text)
        for i, text in enumerate(raw_chunks):
            if not text.strip():
                continue
            prov = ProvenanceDTO(
                source_path=file_path,
                source_mime=mime or detected_mime,
                text_snippet=clean_snippet(text),
            )
            chunk = ChunkDTO(
                document_id=doc_id,
                chunk_id=stable_chunk_id(doc_id, i, None),
                chunk_index=i,
                text=text,
                provenance=prov,
                text_hash=hash_text(text),
            )
            out.chunks.append(chunk)
            _log.debug("  chunk %d: id=%s len=%d", i, chunk.chunk_id, len(text))

    _log.info("Created DocumentDTO with %d chunks", len(out.chunks))
    return out


# Backward-compat alias
extract_with_llamaindex = extract_with_pageindex
