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
from ontorag.verbosity import setup_logging, get_logger

app = typer.Typer(add_completion=False, help="OntoRAG CLI — ingestion, ontology proposals, schema cards, RDF export.")
_log = get_logger("ontorag.cli")


@app.callback()
def _cli_callback(
    verbose: int = typer.Option(0, "--verbose", "-v", count=True,
                                help="Verbosity level: -v for progress, -vv for debug traces."),
):
    """OntoRAG — ontology-first RAG pipeline."""
    setup_logging(verbose)


# -------------------------
# Helpers
# -------------------------

def read_json(path: str) -> dict:
    _log.debug("Reading JSON: %s", path)
    return json.loads(Path(path).read_text(encoding="utf-8"))

def write_json(path: str, obj: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.debug("Wrote JSON: %s", path)

def read_jsonl(path: str) -> List[dict]:
    _log.debug("Reading JSONL: %s", path)
    out = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    _log.debug("Read %d records from %s", len(out), path)
    return out

def write_text(path: str, text: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")
    _log.debug("Wrote text: %s", path)


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
    _log.info("Ingesting file: %s", file)
    doc = extract_with_llamaindex(file, mime=mime)
    _log.info("Storing %d chunks to %s", len(doc.chunks), out)
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
    from ontorag.ontology_extractor_openrouter import extract_schema_chunk_proposals

    chunks_list = read_jsonl(chunks)
    card = read_json(schema_card)

    _log.info("Running schema extraction on %d chunks", len(chunks_list))

    # 1) per-chunk proposals (LLM)
    chunk_proposals = extract_schema_chunk_proposals(chunks_list, card)

    # 2) aggregate document-level
    _log.info("Aggregating %d chunk proposals", len(chunk_proposals))
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

    _log.info("Building schema card: previous=%s proposal=%s", previous, proposal)
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

    _log.info("Exporting schema TTL: namespace=%s", namespace)
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
    _log.info("Uploading TTL %s to graph <%s>", file, graph)
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
    _log.info("Executing SPARQL UPDATE from %s (%d chars)", query_file, len(q))
    _log.debug("Query:\n%s", q)
    blazegraph_sparql_update(q)
    typer.echo("OK sparql-update")


@app.command("extract-instances")
def cmd_extract_instances(
    chunks: str = typer.Option(..., help="Path to chunks JSONL (ChunkDTO records)"),
    schema_card: str = typer.Option(..., help="Path to schema_card.json"),
    out_ttl: str = typer.Option(..., help="Output TTL for instances + provenance"),
):
    """
    DTO chunks -> instance proposals (OpenRouter) -> RDF TTL (instances + provenance).
    """
    from ontorag.instance_extractor_openrouter import extract_instance_chunk_proposals
    from ontorag.instances_to_ttl import instance_proposals_to_graph

    chunks_list = read_jsonl(chunks)
    card = read_json(schema_card)

    # index chunks by chunk_id for provenance
    chunks_by_id = {c.get("chunk_id"): c for c in chunks_list if c.get("chunk_id")}

    _log.info("Extracting instances from %d chunks", len(chunks_list))

    # 1) LLM: per-chunk instance proposals
    proposals = extract_instance_chunk_proposals(chunks_list, card)

    # 2) JSON -> RDF graph
    ns = card.get("namespace") or "http://www.example.com/biz/"
    _log.info("Converting %d proposals to RDF (namespace=%s)", len(proposals), ns)
    g = instance_proposals_to_graph(chunks_by_id, proposals, namespace=ns)

    Path(out_ttl).parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=out_ttl, format="turtle")
    typer.echo(f"OK extract-instances: chunks={len(chunks_list)} out={out_ttl}")


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

    _log.info("Starting SPARQL server on %s:%d", host, port)
    api = create_app(
        ontology_ttl=onto,
        instances_ttl=inst,
        enable_cors=cors,
        cors_allow_origins=cors_origins,
    )

    uvicorn.run(api, host=host, port=port, reload=reload)

@app.command("mcp-server")
def cmd_mcp_server(
    onto: Optional[str] = typer.Option(None, help="Ontology TTL path (local mode)"),
    inst: Optional[str] = typer.Option(None, help="Instances TTL path (local mode)"),
    sparql_endpoint: Optional[str] = typer.Option(None, help="Remote SPARQL endpoint (Blazegraph/QLever)"),
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(9010, help="Bind port"),
):
    """
    Start an MCP server backed by either:
      - local TTL (rdflib in-memory), or
      - a remote SPARQL endpoint (Blazegraph/QLever).
    """
    from ontorag.mcp_backend import LocalRdfBackend, RemoteSparqlBackend
    from ontorag.mcp_server import create_mcp_app

    if sparql_endpoint:
        _log.info("MCP server: remote backend at %s", sparql_endpoint)
        backend = RemoteSparqlBackend(sparql_endpoint)
    else:
        if not onto or not inst:
            raise typer.BadParameter("Provide --sparql-endpoint OR both --onto and --inst")
        _log.info("MCP server: local backend onto=%s inst=%s", onto, inst)
        backend = LocalRdfBackend(onto, inst)

    app_mcp = create_mcp_app(backend)
    _log.info("Starting MCP server on %s:%d", host, port)
    app_mcp.run(host=host, port=port)


@app.command("ontology-mcp")
def cmd_ontology_mcp(
    catalog: str = typer.Option("./data/ontologies", help="Path to ontology catalog directory"),
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(9020, help="Bind port"),
):
    """
    Start an MCP server for browsing, searching, and composing baseline ontologies.

    Tools:
      - list_ontologies     — list registered baselines
      - inspect_ontology    — view classes/properties of a baseline
      - search_classes      — search classes across all baselines
      - search_properties   — search properties across all baselines
      - compose             — merge selected baselines into a schema card
      - add_ontology        — register a new baseline (TTL content)
    """
    from ontorag.ontology_mcp import create_ontology_mcp

    _log.info("Starting ontology catalog MCP on %s:%d (catalog=%s)", host, port, catalog)
    mcp_app = create_ontology_mcp(catalog)
    mcp_app.run(host=host, port=port)


@app.command("register-ontology")
def cmd_register_ontology(
    slug: str = typer.Argument(..., help="Short identifier for the ontology (e.g., 'foaf', 'schema_org')"),
    ttl: str = typer.Argument(..., help="Path to OWL/RDFS Turtle file"),
    catalog: str = typer.Option("./data/ontologies", help="Path to ontology catalog directory"),
    label: str = typer.Option("", help="Human-readable label"),
    description: str = typer.Option("", help="Short description"),
    namespace: Optional[str] = typer.Option(None, help="Override namespace (auto-detected if omitted)"),
    tags: Optional[str] = typer.Option(None, help="Comma-separated tags"),
):
    """
    Register an OWL/TTL file as a baseline ontology in the catalog.
    """
    from ontorag.ontology_catalog import register_ontology

    _log.info("Registering ontology: slug=%s ttl=%s", slug, ttl)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    entry = register_ontology(
        catalog_dir=catalog,
        slug=slug,
        ttl_path=ttl,
        label=label,
        description=description,
        namespace=namespace,
        tags=tag_list,
    )
    typer.echo(f"OK register-ontology: slug={entry['slug']} namespace={entry.get('namespace','')}")


@app.command("init-schema-card")
def cmd_init_schema_card(
    baselines: str = typer.Option(..., help="Comma-separated baseline slugs (e.g., 'foaf,schema_org')"),
    out: str = typer.Option(..., help="Output path for initial schema_card.json"),
    catalog: str = typer.Option("./data/ontologies", help="Path to ontology catalog directory"),
    namespace: Optional[str] = typer.Option(None, help="Override target namespace"),
):
    """
    Create an initial schema card by composing one or more baseline ontologies.
    Each class/property carries an 'origin' field tracking its source.
    """
    from ontorag.ontology_catalog import compose_baselines

    slugs = [s.strip() for s in baselines.split(",") if s.strip()]
    if not slugs:
        raise typer.BadParameter("Provide at least one baseline slug.")

    _log.info("Composing baselines: %s", slugs)
    card = compose_baselines(catalog, slugs, target_namespace=namespace)
    write_json(out, card)

    cls_count = len(card.get("classes", []))
    dp_count = len(card.get("datatype_properties", []))
    op_count = len(card.get("object_properties", []))
    typer.echo(
        f"OK init-schema-card: baselines={slugs} "
        f"classes={cls_count} datatype_props={dp_count} object_props={op_count} out={out}"
    )


def main():
    app()


if __name__ == "__main__":
    main()
