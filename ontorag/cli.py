from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import json
import os
from pathlib import Path
from typing import Optional, List

import typer

from ontorag.extractor_ingest import extract_with_llamaindex
from ontorag.storage_jsonl import store_document_jsonl
from ontorag.schema_card import schema_card_from_proposal
from ontorag.proposal_aggregator import aggregate_chunk_proposals
from ontorag.proposal_to_ttl import proposal_to_ttl
from ontorag.blazegraph import blazegraph_upload_ttl, blazegraph_sparql_update

app = typer.Typer(add_completion=False, help="OntoRAG CLI — ingestion, ontology proposals, schema cards, RDF export.")

# -------------------------
# Helpers
# -------------------------

def read_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def write_json(path: str, obj: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def read_jsonl(path: str) -> List[dict]:
    out = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out

def write_text(path: str, text: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


# -------------------------
# Commands
# -------------------------

@app.command("ingest")
def cmd_ingest(
    file: str = typer.Argument(..., help="Path to the input file (pdf/docx/md/html/csv/...)"),
    out: str = typer.Option("./data/dto", help="Output folder for DTO store"),
    mime: Optional[str] = typer.Option(None, help="Optional MIME type override"),
):
    """
    Ingest a file using LlamaIndex and store DocumentDTO + ChunkDTO (JSON + JSONL).
    """
    doc = extract_with_llamaindex(file, mime=mime)
    store_document_jsonl(doc, out)
    typer.echo(f"OK ingest: document_id={doc.document_id} chunks={len(doc.chunks)} out={out}")


@app.command("extract-schema")
def cmd_extract_schema(
    chunks: str = typer.Option(..., help="Path to chunks JSONL (ChunkDTO records)"),
    schema_card: str = typer.Option(..., help="Path to current schema_card.json"),
    out: str = typer.Option(..., help="Output path for aggregated schema proposal JSON"),
):
    """
    Run ontology induction on DTO chunks and produce an aggregated schema proposal (JSON).
    """
    from ontorag.ontology_extractor_openrouter import extract_schema_chunk_proposals  # implement this module

    chunks_list = read_jsonl(chunks)
    card = read_json(schema_card)

    # 1) per-chunk proposals (LLM)
    chunk_proposals = extract_schema_chunk_proposals(chunks_list, card)

    # 2) aggregate document-level
    aggregated = aggregate_chunk_proposals(chunk_proposals)

    write_json(out, aggregated)
    typer.echo(f"OK extract-schema: chunks={len(chunks_list)} proposals={len(chunk_proposals)} out={out}")


@app.command("build-schema-card")
def cmd_build_schema_card(
    previous: str = typer.Option(..., help="Path to previous schema_card.json"),
    proposal: str = typer.Option(..., help="Path to aggregated schema proposal JSON"),
    out: str = typer.Option(..., help="Output path for next schema_card.json"),
    namespace: Optional[str] = typer.Option(None, help="Override namespace in output schema card"),
):
    """
    Deterministically merge previous schema card with an aggregated proposal → new schema card.
    """
    prev = read_json(previous)
    prop = read_json(proposal)

    new_card = schema_card_from_proposal(prev, prop, namespace=namespace)
    write_json(out, new_card)
    typer.echo(f"OK build-schema-card: out={out}")


@app.command("export-schema-ttl")
def cmd_export_schema_ttl(
    proposal: str = typer.Option(..., help="Path to aggregated schema proposal JSON"),
    out: str = typer.Option(..., help="Output path for TTL"),
    namespace: str = typer.Option("http://www.example.com/biz/", help="Base namespace for generated terms"),
):
    """
    Export a schema proposal JSON into a staging OWL/RDFS Turtle file.
    """
    prop = read_json(proposal)
    g = proposal_to_ttl(prop, biz_ns=namespace)

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=out, format="turtle")
    typer.echo(f"OK export-schema-ttl: out={out}")


@app.command("load-ttl")
def cmd_load_ttl(
    file: str = typer.Option(..., help="Path to a TTL file to upload to Blazegraph"),
    graph: str = typer.Option(..., help="Named graph IRI (e.g., urn:staging:schema)"),
):
    """
    Upload a TTL file into Blazegraph into a specific named graph.
    Requires BLAZEGRAPH_ENDPOINT env var (SPARQL endpoint).
    """
    blazegraph_upload_ttl(file, graph)
    typer.echo(f"OK load-ttl: file={file} graph={graph}")


@app.command("sparql-update")
def cmd_sparql_update(
    query_file: str = typer.Option(..., help="Path to SPARQL UPDATE file"),
):
    """
    Execute a SPARQL UPDATE against Blazegraph. Dangerous if you point at prod.
    """
    q = Path(query_file).read_text(encoding="utf-8")
    blazegraph_sparql_update(q)
    typer.echo("OK sparql-update")


# Optional (stub) — da implementare dopo
@app.command("extract-instances")
def cmd_extract_instances(
    chunks: str = typer.Option(..., help="Path to chunks JSONL (ChunkDTO records)"),
    schema_card: str = typer.Option(..., help="Path to schema_card.json"),
    out_ttl: str = typer.Option(..., help="Output TTL for instances"),
):
    """
    Instance extraction (DTO chunks -> RDF instances + provenance).
    """
    raise typer.Exit(code=1)

    @app.command("sparql-server")
def cmd_sparql_server(
    onto: Optional[str] = typer.Option(None, help="Ontology TTL path (default: env ONTOLOGY_TTL)"),
    inst: Optional[str] = typer.Option(None, help="Instances TTL path (default: env INSTANCES_TTL)"),
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8890, help="Bind port"),
    cors: bool = typer.Option(True, help="Enable CORS"),
    cors_origins: str = typer.Option("*", help="Comma-separated allowed origins"),
    reload: bool = typer.Option(False, help="Uvicorn auto-reload (dev only)"),
):
    """
    Start a local in-memory SPARQL endpoint (FastAPI) to inspect ontology + instances TTL.
    Endpoints:
      - GET/POST /sparql
      - GET /health
      - GET /stats
      - POST /reload
    """
    import uvicorn
    from ontorag.sparql_server import create_app

    api = create_app(
        ontology_ttl=onto,
        instances_ttl=inst,
        enable_cors=cors,
        cors_allow_origins=cors_origins,
    )

    uvicorn.run(api, host=host, port=port, reload=reload)



def main():
    app()


if __name__ == "__main__":
    main()
