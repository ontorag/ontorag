# dto.py
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import hashlib

class ProvenanceDTO(BaseModel):
    source_path: str
    source_mime: Optional[str] = None

    page: Optional[int] = None
    page_label: Optional[str] = None
    section: Optional[str] = None

    offset_start: Optional[int] = None
    offset_end: Optional[int] = None

    text_snippet: Optional[str] = None

    raw: Dict[str, Any] = Field(default_factory=dict)


class ChunkDTO(BaseModel):
    document_id: str
    chunk_id: str
    chunk_index: int

    text: str
    provenance: ProvenanceDTO

    text_hash: str

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class DocumentDTO(BaseModel):
    document_id: str
    source_path: str
    source_mime: Optional[str] = None

    title: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    chunks: List[ChunkDTO] = Field(default_factory=list)


def stable_document_id(source_path: str) -> str:
    h = hashlib.sha1(source_path.encode("utf-8")).hexdigest()[:12]
    return f"doc_{h}"


def stable_chunk_id(document_id: str, chunk_index: int, page: Optional[int]) -> str:
    p = f"p{page}" if page is not None else "pNA"
    return f"{document_id}#{p}#c{chunk_index:04d}"


def hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()
