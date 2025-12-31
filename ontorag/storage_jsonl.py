# storage_jsonl.py
from __future__ import annotations
from pathlib import Path
from ontorag.dto import DocumentDTO
import json

def store_document_jsonl(doc: DocumentDTO, out_dir: str) -> str:
    """
    Salva:
      - doc meta: documents/<document_id>.json
      - chunks: chunks/<document_id>.jsonl
    """
    base = Path(out_dir)
    (base / "documents").mkdir(parents=True, exist_ok=True)
    (base / "chunks").mkdir(parents=True, exist_ok=True)

    doc_path = base / "documents" / f"{doc.document_id}.json"
    chunks_path = base / "chunks" / f"{doc.document_id}.jsonl"

    doc_meta = doc.model_dump()
    doc_meta["chunks"] = []
    doc_path.write_text(json.dumps(doc_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # chunks
    with chunks_path.open("w", encoding="utf-8") as f:
        for ch in doc.chunks:
            f.write(json.dumps(ch.model_dump(), ensure_ascii=False) + "\n")

    return str(base)
