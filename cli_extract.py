# cli_extract.py
import argparse
from extractor import extract_with_llamaindex
from storage_jsonl import store_document_jsonl

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="Path to file to ingest")
    ap.add_argument("--out", default="./dto_store", help="Output folder")
    ap.add_argument("--mime", default=None, help="Optional mime type")
    args = ap.parse_args()

    doc = extract_with_llamaindex(args.file, mime=args.mime)
    store_document_jsonl(doc, args.out)

    print(f"OK: {doc.document_id} chunks={len(doc.chunks)} stored in {args.out}")

if __name__ == "__main__":
    main()
