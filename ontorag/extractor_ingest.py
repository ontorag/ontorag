# extractor.py
from __future__ import annotations
from typing import Optional
from ontorag.dto import (
    DocumentDTO, ChunkDTO, ProvenanceDTO,
    stable_document_id, stable_chunk_id, hash_text
)
from ontorag.verbosity import get_logger

_log = get_logger("ontorag.extractor_ingest")

def clean_snippet(text: str, max_len: int = 240) -> str:
    t = " ".join(text.split())
    return (t[:max_len] + "…") if len(t) > max_len else t


def extract_with_llamaindex(file_path: str, mime: Optional[str] = None) -> DocumentDTO:
    from llama_index.core import SimpleDirectoryReader
    from llama_index.core.node_parser import SentenceSplitter

    doc_id = stable_document_id(file_path)
    _log.info("Ingesting %s (doc_id=%s)", file_path, doc_id)

    docs = SimpleDirectoryReader(input_files=[file_path]).load_data()
    _log.debug("LlamaIndex loaded %d raw documents", len(docs))

    splitter = SentenceSplitter(chunk_size=1024, chunk_overlap=120)
    nodes = splitter.get_nodes_from_documents(docs)
    _log.info("Split into %d chunks (chunk_size=1024, overlap=120)", len(nodes))

    out = DocumentDTO(
        document_id=doc_id,
        source_path=file_path,
        source_mime=mime,
        title=None,
        chunks=[]
    )

    for i, node in enumerate(nodes):
        text = node.get_content() if hasattr(node, "get_content") else str(node.text)

        meta = {}
        if hasattr(node, "metadata") and isinstance(node.metadata, dict):
            meta = node.metadata

        # best-effort provenance
        page = meta.get("page") or meta.get("page_number")
        page_label = meta.get("page_label")
        section = meta.get("section") or meta.get("header")

        prov = ProvenanceDTO(
            source_path=file_path,
            source_mime=mime,
            page=int(page) if page is not None and str(page).isdigit() else None,
            page_label=str(page_label) if page_label is not None else None,
            section=str(section) if section is not None else None,
            offset_start=meta.get("offset_start"),
            offset_end=meta.get("offset_end"),
            text_snippet=clean_snippet(text),
            raw=meta
        )

        chunk = ChunkDTO(
            document_id=doc_id,
            chunk_id=stable_chunk_id(doc_id, i, prov.page),
            chunk_index=i,
            text=text,
            provenance=prov,
            text_hash=hash_text(text)
        )
        out.chunks.append(chunk)
        _log.debug("  chunk %d: id=%s len=%d page=%s", i, chunk.chunk_id, len(text), prov.page)

    _log.info("Created DocumentDTO with %d chunks", len(out.chunks))
    return out
