from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import json
import os
from pathlib import Path
from typing import Optional, List

import typer

from ontorag.dto import stable_document_id, hash_file
from ontorag.extractor_ingest import extract_document
from ontorag.storage_jsonl import store_document_jsonl
from ontorag.schema_card import schema_card_from_proposal
from ontorag.proposal_aggregator import aggregate_chunk_proposals
from ontorag.proposal_to_ttl import proposal_to_ttl
from ontorag.blazegraph import blazegraph_upload_ttl, blazegraph_sparql_update
from ontorag.verbosity import setup_logging, get_logger
from ontorag import llm_config

app = typer.Typer(add_completion=False, help="OntoRAG CLI — ingestion, ontology proposals, schema cards, RDF export.")
_log = get_logger("ontorag.cli")


def _version_callback(value: bool):
    if value:
        from importlib.metadata import version
        try:
            typer.echo(f"ontorag {version('ontorag')}")
        except Exception:
            typer.echo("ontorag (version unknown — not installed as a distribution)")
        raise typer.Exit()


@app.callback()
def _cli_callback(
    version: bool = typer.Option(None, "--version", "-V", callback=_version_callback,
                                is_eager=True,
                                help="Print the installed ontorag version and exit."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True,
                                help="Verbosity level: -v for progress, -vv for debug traces."),
    model: Optional[str] = typer.Option(None, "--model", "-m",
                                help="OpenRouter model for extract-schema / align-schema / extract-instances (overrides OPENROUTER_MODEL)."),
    api_key: Optional[str] = typer.Option(None, "--api-key",
                                help="OpenRouter API key (overrides OPENROUTER_API_KEY)."),
    base_url: Optional[str] = typer.Option(None, "--base-url",
                                help="OpenAI-compatible base URL (overrides OPENROUTER_BASE_URL); point at a local server, e.g. http://localhost:11434/v1 for ollama."),
    app_name: Optional[str] = typer.Option(None, "--app-name",
                                help="X-Title header sent to OpenRouter (overrides OPENROUTER_APP_NAME)."),
    site_url: Optional[str] = typer.Option(None, "--site-url",
                                help="HTTP-Referer header sent to OpenRouter (overrides OPENROUTER_SITE_URL)."),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", "-j",
                                help="Parallel LLM requests for extract-schema/extract-instances (default 4; 1 = sequential)."),
    slim_card: bool = typer.Option(False, "--slim-card",
                                help="Prune the schema card to chunk-relevant terms in each prompt (smaller/cheaper prompts; may lower instance recall on large baselines)."),
):
    """OntoRAG — ontology-first RAG pipeline.

    LLM settings may be given as global flags before the subcommand, or via the
    environment: `ontorag --model <m> --api-key <k> extract-schema ...`.
    Precedence: CLI flag > environment variable > built-in default.
    """
    setup_logging(verbose)
    llm_config.set_overrides(
        model=model, api_key=api_key, base_url=base_url,
        app_name=app_name, site_url=site_url,
    )
    from ontorag.card_slim import set_slim
    from ontorag.parallel import set_concurrency
    set_slim(slim_card)
    set_concurrency(concurrency)


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
            rec = json.loads(line)
            if rec is None:
                # `null` lines exist in raw-proposal stores written before failed
                # chunks were dropped; reading them back would re-poison the run
                continue
            out.append(rec)
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
    file: str = typer.Argument(..., help="Input file, or a directory to ingest every file in"),
    out: str = typer.Option("./data/dto", help="Output folder for DTO store"),
    mime: Optional[str] = typer.Option(None, help="Optional MIME type override"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-ingest even if the file was already processed"),
    engine: str = typer.Option(
        "builtin", "--engine", "-e", envvar="ONTORAG_INGEST_ENGINE",
        help="Ingestion engine: builtin (default, no deps/keys) | pageindex (hosted "
             "hierarchical PDF) | llamaindex | docling (layout-aware, self-hosted) | "
             "unstructured. Non-builtin engines need their extra — see `ontorag doctor`."),
):
    """
    Ingest a file and store DocumentDTO + ChunkDTO (JSON + JSONL).

    Engines (choose with --engine or ONTORAG_INGEST_ENGINE; `ontorag doctor` shows
    which are installed):

      builtin      — no deps, no key: md/text local splitter, epub/html, pdf via PyMuPDF [default]
      pageindex    — hierarchical section detection (hosted PageIndex API for PDF)
      llamaindex   — LlamaIndex fixed-size chunking
      docling      — IBM Docling: layout-aware PDF/DOCX/PPTX → Markdown, self-hosted
      unstructured — Unstructured: broad-format partitioning into typed elements

    Documents are content-hashed (SHA-256) before chunking. If the same content
    was already ingested, the command skips processing. Use --force to re-ingest.
    """
    from ontorag.extractor_ingest import ENGINES
    if engine not in ENGINES:
        raise typer.BadParameter(
            f"unknown engine {engine!r}. Choose from: {', '.join(ENGINES)}.", param_hint="--engine")

    # A directory is ingested in this process, one file at a time. The caller could
    # loop the command instead -- and did -- but then every document pays a fresh
    # interpreter and import of rdflib and friends: about 0.5s each, which is 48
    # minutes of pure overhead on a 5,000-document corpus and nothing else.
    if Path(file).is_dir():
        files = sorted(p for p in Path(file).iterdir() if p.is_file() and not p.name.startswith("."))
        if not files:
            raise typer.BadParameter(f"{file} contains no files", param_hint="FILE")
        done = skipped = chunks = 0
        for f in files:
            doc_id = stable_document_id(str(f))
            if (Path(out) / "documents" / f"{doc_id}.json").exists() and not force:
                skipped += 1
                continue
            doc = extract_document(str(f), mime=mime, engine=engine)
            store_document_jsonl(doc, out)
            chunks += len(doc.chunks)
            done += 1
        typer.echo(f"OK ingest: {done} document(s), {chunks} chunk(s), {skipped} already "
                   f"ingested, engine={engine} out={out}")
        return

    doc_id = stable_document_id(file)
    content_hash = hash_file(file)
    _log.info("File %s -> doc_id=%s content_hash=%s engine=%s", file, doc_id, content_hash[:12], engine)

    existing_doc = Path(out) / "documents" / f"{doc_id}.json"
    if existing_doc.exists() and not force:
        typer.echo(f"SKIP ingest: {file} already ingested (document_id={doc_id}, hash={content_hash[:12]}…). Use --force to re-ingest.")
        return

    _log.info("Ingesting file: %s (engine=%s)", file, engine)
    doc = extract_document(file, mime=mime, engine=engine)
    _log.info("Storing %d chunks to %s", len(doc.chunks), out)
    store_document_jsonl(doc, out)
    typer.echo(f"OK ingest: document_id={doc.document_id} chunks={len(doc.chunks)} engine={engine} hash={content_hash[:12]} out={out}")


@app.command("extract-schema")
def cmd_extract_schema(
    chunks: str = typer.Option(..., help="Path to chunks JSONL (ChunkDTO records)"),
    schema_card: str = typer.Option(..., help="Path to current schema_card.json"),
    out: str = typer.Option(..., help="Output path for aggregated schema proposal JSON"),
    raw_in: Optional[str] = typer.Option(None, "--raw-in", help="Per-chunk proposals kept from previous runs (JSONL), aggregated alongside this run"),
    raw_out: Optional[str] = typer.Option(None, "--raw-out", help="Append this run's per-chunk proposals to this JSONL store"),
):
    """
    Run ontology induction on DTO chunks and produce an aggregated schema proposal (JSON).

    For incremental extraction, keep the per-chunk proposals with --raw-out and feed
    them back with --raw-in: the LLM then runs only over the chunks you pass, while
    the aggregate is rebuilt from everything ever proposed.
    """
    from ontorag.ontology_extractor_openrouter import extract_schema_chunk_proposals

    chunks_list = read_jsonl(chunks)
    card = read_json(schema_card)
    total = len(chunks_list)

    typer.echo(f"extract-schema: {total} chunks to process")

    # Running totals for the progress display
    totals = {"classes": 0, "dt_props": 0, "obj_props": 0, "warnings": 0}

    def _on_chunk(idx: int, total: int, chunk_id: str, data: dict) -> None:
        adds = data.get("proposed_additions") or {}
        n_cls = len(adds.get("classes") or [])
        n_dp = len(adds.get("datatype_properties") or [])
        n_op = len(adds.get("object_properties") or [])
        n_warn = len(data.get("warnings") or [])

        totals["classes"] += n_cls
        totals["dt_props"] += n_dp
        totals["obj_props"] += n_op
        totals["warnings"] += n_warn

        typer.echo(
            f"  [{idx + 1}/{total}] chunk {chunk_id}  "
            f"+{n_cls} classes  +{n_dp} dt_props  +{n_op} obj_props  "
            f"{n_warn} warnings  "
            f"(cumulative: {totals['classes']}C {totals['dt_props']}D {totals['obj_props']}O {totals['warnings']}W)"
        )

    # The raw store is written as chunks land, not after the loop.
    #
    # Induction over a large corpus is hours of paid LLM calls, and this step used
    # to hold every proposal in memory until the end: one exception after the last
    # chunk -- which is exactly what a `None` from a failed chunk used to cause --
    # threw away the whole run's work and its cost. Appending per chunk means a
    # crash loses the chunk, not the corpus.
    previous: List[dict] = []
    if raw_in and Path(raw_in).is_file():
        previous = read_jsonl(raw_in)
        typer.echo(f"  {len(previous)} proposal(s) kept from previous runs")

    raw_fh = None
    if raw_out:
        Path(raw_out).parent.mkdir(parents=True, exist_ok=True)
        raw_fh = open(raw_out, "a", encoding="utf-8")

    def _checkpoint(idx: int, total_: int, chunk_id: str, data: dict) -> None:
        _on_chunk(idx, total_, chunk_id, data)
        if raw_fh is not None:
            raw_fh.write(json.dumps(data, ensure_ascii=False) + "\n")
            raw_fh.flush()

    # 1) per-chunk proposals (LLM)
    try:
        chunk_proposals = extract_schema_chunk_proposals(
            chunks_list, card, on_chunk_done=_checkpoint)
    finally:
        if raw_fh is not None:
            raw_fh.close()

    # 2) aggregate document-level
    #
    # Incrementality works by keeping the *per-chunk* proposals rather than merging
    # aggregates: aggregation is deterministic and cheap, so rebuilding it from
    # everything ever proposed keeps one code path and one meaning of "the
    # proposal", whether or not this run was incremental.
    if raw_out:
        typer.echo(f"  kept {len(chunk_proposals)} per-chunk proposal(s) in {raw_out}")

    # `previous` was read before the run, so no trimming games are needed when
    # --raw-in and --raw-out are the same file.
    all_proposals = previous + list(chunk_proposals)
    if previous:
        typer.echo(f"  aggregating {len(previous)} previous + {len(chunk_proposals)} new")

    # Which chunks the LLM never produced anything usable for. Recorded so the
    # stage can leave those documents out of its ledger and retry them next run,
    # instead of marking a document done that nothing was extracted from.
    from ontorag import ontology_extractor_openrouter as _ose

    if _ose.LAST_SKIPPED:
        skip_path = Path(out).parent / "skipped_chunks.json"
        existing = read_json(str(skip_path)) if skip_path.is_file() else {}
        existing["extract-schema"] = sorted(set(existing.get("extract-schema", []))
                                            | set(_ose.LAST_SKIPPED))
        write_json(str(skip_path), existing)
        typer.echo(f"  {len(_ose.LAST_SKIPPED)} chunk(s) produced nothing usable "
                   f"-> recorded in {skip_path}")

    typer.echo(f"Aggregating {len(all_proposals)} chunk proposals...")
    aggregated = aggregate_chunk_proposals(all_proposals)

    agg_cls = len(aggregated.get("classes", []))
    agg_dp = len(aggregated.get("datatype_properties", []))
    agg_op = len(aggregated.get("object_properties", []))
    agg_warn = len(aggregated.get("warnings", []))

    write_json(out, aggregated)
    typer.echo(
        f"OK extract-schema: {total} chunks -> "
        f"{agg_cls} classes, {agg_dp} dt_props, {agg_op} obj_props, "
        f"{agg_warn} warnings  out={out}"
    )


@app.command("align-schema")
def cmd_align_schema(
    proposal: str = typer.Option(..., help="Path to aggregated schema proposal JSON (output of extract-schema)"),
    baseline: str = typer.Option(..., help="Path to baseline schema_card.json (output of init-schema-card)"),
    out: str = typer.Option(..., help="Output path for alignment JSON"),
):
    """
    Align induced schema proposals against baseline ontologies.

    Compares the classes and properties from extract-schema against the
    baseline schema card and produces an alignment mapping.  Each induced
    item gets an action (reuse / extend / new), a link to the matching
    baseline item and its source ontology, plus a confidence score.

    Only meaningful when baselines were used in init-schema-card.
    """
    from ontorag.schema_alignment import align_schema

    prop = read_json(proposal)
    base = read_json(baseline)

    n_induced = (
        len(prop.get("classes", []))
        + len(prop.get("datatype_properties", []))
        + len(prop.get("object_properties", []))
    )
    n_baseline = (
        len(base.get("classes", []))
        + len(base.get("datatype_properties", []))
        + len(base.get("object_properties", []))
    )

    if n_baseline == 0:
        typer.echo("align-schema: baseline has no classes or properties — skipping alignment")
        write_json(out, {"classes": [], "datatype_properties": [], "object_properties": [], "warnings": ["No baselines to align against."]})
        return

    typer.echo(
        f"align-schema: {n_induced} induced items vs {n_baseline} baseline items"
    )

    # ── Resume: load prior partial results if they exist ──
    prior = None
    if os.path.exists(out):
        try:
            prior_data = read_json(out)
            if prior_data.get("_partial"):
                has = [k for k in ("classes", "datatype_properties", "object_properties") if prior_data.get(k)]
                typer.echo(f"align-schema: resuming from partial {out}  (have {', '.join(has) or 'nothing'})")
                prior = prior_data
        except Exception:
            pass  # ignore corrupt file, start fresh

    def _on_category(cat_key: str, cat_result: dict, *, resumed: bool = False) -> None:
        tag = " (resumed)" if resumed else ""
        items = cat_result.get("alignments", [])
        reuse = sum(1 for a in items if a.get("action") == "reuse")
        extend = sum(1 for a in items if a.get("action") == "extend")
        new = sum(1 for a in items if a.get("action") == "new")
        typer.echo(
            f"  {cat_key}: {len(items)} aligned  "
            f"(reuse={reuse}  extend={extend}  new={new}){tag}"
        )
        for a in items:
            action = a.get("action", "?")
            conf = a.get("confidence", "?")
            iname = a.get("induced_name", "?")
            bname = a.get("baseline_name", "")
            borig = a.get("baseline_origin", "")
            rationale = a.get("rationale", "")
            if action == "new":
                typer.echo(f"    {iname} -> new  ({conf})  {rationale}")
            else:
                typer.echo(f"    {iname} -> {action} {bname} [{borig}]  ({conf})  {rationale}")

    def _on_flush(partial: dict) -> None:
        write_json(out, partial)

    alignment = align_schema(prop, base, on_category_done=_on_category, on_flush=_on_flush, prior=prior)

    write_json(out, alignment)

    total_reuse = sum(
        1 for cat in ("classes", "datatype_properties", "object_properties")
        for a in alignment.get(cat, []) if a.get("action") == "reuse"
    )
    total_extend = sum(
        1 for cat in ("classes", "datatype_properties", "object_properties")
        for a in alignment.get(cat, []) if a.get("action") == "extend"
    )
    total_new = sum(
        1 for cat in ("classes", "datatype_properties", "object_properties")
        for a in alignment.get(cat, []) if a.get("action") == "new"
    )
    n_warn = len(alignment.get("warnings", []))

    typer.echo(
        f"OK align-schema: reuse={total_reuse} extend={total_extend} "
        f"new={total_new} warnings={n_warn}  out={out}"
    )


@app.command("build-schema-card")
def cmd_build_schema_card(
    previous: str = typer.Option(..., help="Path to previous schema_card.json"),
    proposal: str = typer.Option(..., help="Path to aggregated schema proposal or alignment JSON"),
    out: str = typer.Option(..., help="Output path for next schema_card.json"),
    namespace: Optional[str] = typer.Option(None, help="Override namespace in output schema card"),
    original_proposal: Optional[str] = typer.Option(None, "--original-proposal",
        help="Path to the original proposal JSON (for descriptions when building from alignment)"),
):
    """
    Deterministically merge previous schema card with an aggregated proposal → new schema card.

    Accepts both raw proposals (output of extract-schema) and alignment
    results (output of align-schema).  When building from alignment data,
    pass --original-proposal to preserve descriptions and range types.
    """
    from ontorag.alignment_normalizer import normalize_alignment

    prev = read_json(previous)
    prop = read_json(proposal)
    orig = read_json(original_proposal) if original_proposal else None
    prop = normalize_alignment(prop, original_proposal=orig)

    _log.info("Building schema card: previous=%s proposal=%s", previous, proposal)
    new_card = schema_card_from_proposal(prev, prop, namespace=namespace)
    write_json(out, new_card)
    typer.echo(f"OK build-schema-card: out={out}")


@app.command("export-schema-ttl")
def cmd_export_schema_ttl(
    proposal: str = typer.Option(..., help="Path to aggregated schema proposal JSON or alignment JSON"),
    out: str = typer.Option(..., help="Output path for TTL"),
    namespace: str = typer.Option("http://www.example.com/biz/", help="Base namespace for generated (local) terms"),
    prefix: str = typer.Option("biz", help="Prefix label bound to the local namespace in the TTL"),
    catalog: str = typer.Option("./data/ontologies", help="Ontology catalog dir — resolves baseline slugs (e.g. 'rpg') to their real IRIs on aligned terms"),
    baseline: Optional[str] = typer.Option(None, "--baseline",
        help="Baseline schema_card.json — lets baseline classes referenced only as a domain/range (e.g. rpg:Actor) resolve to their origin IRI too"),
    original_proposal: Optional[str] = typer.Option(None, "--original-proposal",
        help="Path to the original proposal JSON (for descriptions when exporting from alignment)"),
):
    """
    Export a schema proposal or alignment JSON into a staging OWL/RDFS Turtle file.

    Accepts both raw proposals (output of extract-schema) and alignment results
    (output of align-schema).  When exporting alignment data, pass
    --original-proposal to preserve descriptions and range types.

    If the ontology catalog is available, terms aligned to a baseline ontology
    are exported with their **origin IRI** — e.g. a `Magus` aligned as a subclass
    of the `rpg` baseline `Character` becomes `local:Magus rdfs:subClassOf
    rpg:Character` (interoperable), not a local copy of Character.
    """
    from ontorag.ontology_catalog import catalog_prefixes

    prop = read_json(proposal)
    orig = read_json(original_proposal) if original_proposal else None
    base_card = read_json(baseline) if baseline else None
    prefixes = catalog_prefixes(catalog)

    _log.info("Exporting schema TTL: namespace=%s prefix=%s baselines=%s", namespace, prefix, list(prefixes))
    g = proposal_to_ttl(prop, biz_ns=namespace, original_proposal=orig,
                        prefixes=prefixes, local_prefix=prefix, baseline_card=base_card)

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=out, format="turtle")
    typer.echo(f"OK export-schema-ttl: out={out}  baselines_resolved={list(prefixes) or 'none'}")


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


@app.command("run-stage")
def cmd_run_stage(
    stage: str = typer.Argument(..., help="Pipeline stage: propose | extract"),
    root: Optional[str] = typer.Option(None, "--root", help="Dataset directory (default: cwd)"),
    full: bool = typer.Option(False, "--full", help="Re-process every document, ignoring what previous runs already extracted"),
):
    """
    Run one governed pipeline stage over a dataset directory.

    This is the stage composition the Hub's Actions workflow and the desktop client
    both drive, so there is exactly one definition of what `propose` and `extract`
    mean. A frozen single-file build re-invokes itself, so a packaged app can run a
    stage with no Python installed.

    Extraction is incremental: only documents no previous run has processed are sent
    to the LLM, so adding one file to a large corpus costs one file. Use --full to
    re-process everything (a changed schema card forces this for instances anyway).

    Adding files is exact. Editing one re-extracts it, but does not retract what its
    previous version contributed to the graph — run --full after edits.
    """
    from ontorag.run_stage import run

    run(stage, root, full=full)


@app.command("load-neo4j")
def cmd_load_neo4j(
    file: str = typer.Option(..., help="Path to an instances TTL (e.g. ontology/world.ttl)"),
    uri: Optional[str] = typer.Option(None, help="Bolt URI (default: env NEO4J_URI, else bolt://localhost:7687)"),
    user: Optional[str] = typer.Option(None, help="Username (default: env NEO4J_USER, else neo4j)"),
    password: Optional[str] = typer.Option(None, help="Password (default: env NEO4J_PASSWORD)"),
    database: Optional[str] = typer.Option(None, help="Target database (default: env NEO4J_DATABASE, else the server default)"),
    wipe: bool = typer.Option(False, "--wipe", help="DESTRUCTIVE: delete existing :Resource/:Mention nodes first"),
    batch: int = typer.Option(1000, help="Rows per UNWIND batch"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Map and report counts without connecting to Neo4j"),
):
    """
    Project an instances TTL into Neo4j as a native property graph.

    Neo4j is a parallel serving target, not a SPARQL store: world.ttl stays canonical.
    Instances become labelled nodes, object properties become relationships, and PROV
    mention nodes become (:Mention) linked by [:DERIVED_FROM]. Re-running is
    idempotent (everything MERGEs on a stable key). Needs the optional neo4j extra.
    """
    from rdflib import Graph as _Graph
    from ontorag.neo4j_store import graph_to_rows, load_rows

    g = _Graph()
    g.parse(file, format="turtle")
    _log.info("Parsed %s (%d triples)", file, len(g))
    rows = graph_to_rows(g)

    if dry_run:
        typer.echo(f"OK load-neo4j (dry-run): file={file} "
                   f"nodes={len(rows['nodes'])} rels={len(rows['rels'])} "
                   f"mentions={len(rows['mentions'])}")
        return

    counts = load_rows(rows, uri=uri, user=user, password=password,
                       database=database, wipe=wipe, batch=batch)
    typer.echo(f"OK load-neo4j: file={file} nodes={counts['nodes']} "
               f"rels={counts['rels']} mentions={counts['mentions']}")


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
    merge: bool = typer.Option(False, "--merge", help="Union into an existing out-ttl rather than replacing it (incremental extraction)"),
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

    from ontorag import instance_extractor_openrouter as _ise

    if _ise.LAST_SKIPPED:
        skip_path = Path(out_ttl).parent / "skipped_chunks.json"
        existing = read_json(str(skip_path)) if skip_path.is_file() else {}
        existing["extract-instances"] = sorted(set(existing.get("extract-instances", []))
                                               | set(_ise.LAST_SKIPPED))
        write_json(str(skip_path), existing)
        typer.echo(f"  {len(_ise.LAST_SKIPPED)} chunk(s) produced nothing usable "
                   f"-> recorded in {skip_path}")

    # 2) JSON -> RDF graph
    ns = card.get("namespace") or "http://www.example.com/biz/"
    _log.info("Converting %d proposals to RDF (namespace=%s)", len(proposals), ns)
    g = instance_proposals_to_graph(chunks_by_id, proposals, namespace=ns)

    if merge and Path(out_ttl).is_file():
        # Instance IRIs derive from (class, label, chunk_id) and mention keys from
        # their quote, so the union is idempotent: re-extracting a document that is
        # already in the graph contributes nothing new.
        new_triples = len(g)
        g.parse(out_ttl, format="turtle")
        typer.echo(f"  merged into existing graph: {new_triples} new, {len(g)} total")

    Path(out_ttl).parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=out_ttl, format="turtle")
    typer.echo(f"OK extract-instances: chunks={len(chunks_list)} out={out_ttl}")


@app.command("index")
def cmd_index(
    dataset_dir: str = typer.Argument(".", help="Dataset directory (contains embeddings/)."),
    out: Optional[str] = typer.Option(None, "--out", help="Output path (default: <dataset>/.ontorag/vectors.db)."),
    quantize: str = typer.Option("turbo4", "--quantize",
                                 help="turbo4|turbo3|turbo2|int8|1bit|none — index size vs recall."),
    chunks: bool = typer.Option(True, "--chunks/--no-chunks",
                                help="Carry chunk text so the file answers a query on its own."),
    ship: bool = typer.Option(False, "--ship",
                              help="Write the publishable form: quantised index, full-precision vectors dropped."),
    force: bool = typer.Option(False, "--force", help="Rebuild even if the shards have not changed."),
    download: bool = typer.Option(False, "--download",
                                  help="Fetch the sqlite-vector extension once into ~/.cache/ontorag."),
):
    """
    Build the query form of a dataset's vectors: one SQLite file, searchable.

    The committed form stays the per-document JSONL shards -- diffable and
    incremental. This is a *cache* built from them, so that a consumer answers a
    nearest-neighbour query by opening a file instead of loading every vector into
    memory first (measured on a 17,942-chunk corpus: 46 ms and 27 MB of RSS,
    against 5.7 s and 555 MB for the dict it replaces).

    `--ship` writes the other useful artefact: the quantised index without the
    full-precision column -- 9 MB where the shards are 144 MB, 33 MB with the chunk
    text -- small enough to attach to a release so that consumers, including ones
    who cannot clone a private dataset, reuse the vectors instead of rebuilding
    them. It is derived data either way: never commit it into the dataset.
    """
    from ontorag import vector_index as vi

    try:
        ext = vi.extension_path(download=download)
        res = vi.build(dataset_dir, out=out, quantize=quantize, with_chunks=chunks,
                       ship=ship, force=force, extension=ext)
    except vi.VectorIndexError as e:
        raise typer.BadParameter(str(e))

    if res.skipped:
        typer.echo(f"OK index: unchanged ({res.vectors} vectors, {res.bytes/1e6:.1f} MB) "
                   f"at {res.path} — use --force to rebuild")
        return
    typer.echo(
        f"OK index: {res.vectors} vectors"
        + (f", {res.chunks} chunk texts" if res.chunks else "")
        + (f", {res.quantized} quantised ({quantize})" if res.quantized else "")
        + f" in {res.seconds:.1f}s -> {res.path} ({res.bytes/1e6:.1f} MB)"
        + (" [shippable]" if ship else "")
    )


@app.command("index-graph")
def cmd_index_graph(
    dataset_dir: str = typer.Argument(".", help="Dataset directory (contains ontology/world.ttl)."),
    graph: List[str] = typer.Option([], "--graph", help="Instance TTL(s) to project, relative to the dataset dir (repeatable; default: ontology/world.ttl)."),
    out: Optional[str] = typer.Option(None, "--out", help="Output .rl file (default: <dataset>/.ontorag/graph.rl)."),
    force: bool = typer.Option(False, "--force", help="Rebuild even if the inputs are unchanged."),
):
    """
    Build the query form of a dataset's graph: an embedded relate store (.rl).

    world.ttl stays the committed source of truth; this is a derived cache (gitignored,
    stamped, rebuilt only when the TTL changes) that `mcp-server --relate` opens in
    a fraction of a second, instead of re-parsing the Turtle, and that answers graph
    search -- shortest paths, neighbourhoods, Cypher. Never commit it. Needs the
    optional relate extra.
    """
    from ontorag import relate_store as rs

    d = Path(dataset_dir)
    sources = [d / g for g in (graph or [rs.DEFAULT_GRAPH])]
    try:
        res = rs.build(sources, out or d / rs.DEFAULT_REL, force=force)
    except rs.RelateStoreError as e:
        raise typer.BadParameter(str(e))
    if res.skipped:
        typer.echo(f"OK index-graph: unchanged ({res.nodes} nodes, {res.rels} rels) "
                   f"at {res.path} — use --force to rebuild")
        return
    typer.echo(f"OK index-graph: {res.nodes} nodes, {res.rels} rels, {res.mentions} mentions "
               f"in {res.seconds:.1f}s -> {res.path} ({res.bytes/1e6:.1f} MB)")


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
    neo4j: bool = typer.Option(False, "--neo4j", help="Serve from a Neo4j projection (see load-neo4j)"),
    relate: Optional[str] = typer.Option(None, "--relate", help="Serve from an embedded relate store: a .rl file, or a dataset dir (its .ontorag/graph.rl is built/refreshed first; see index-graph)"),
    uri: Optional[str] = typer.Option(None, help="Neo4j bolt URI (default: env NEO4J_URI)"),
    user: Optional[str] = typer.Option(None, help="Neo4j username (default: env NEO4J_USER)"),
    password: Optional[str] = typer.Option(None, help="Neo4j password (default: env NEO4J_PASSWORD)"),
    database: Optional[str] = typer.Option(None, help="Neo4j database (default: env NEO4J_DATABASE)"),
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(9010, help="Bind port"),
):
    """
    Start an MCP server backed by either:
      - local TTL (rdflib in-memory),
      - a remote SPARQL endpoint (Blazegraph/QLever), or
      - a Neo4j projection (--neo4j; needs the optional neo4j extra), or
      - an embedded relate store (--relate; needs the optional relate extra).

    The navigation tools (describe, list_by_class, outgoing, incoming, mentions) are
    the same whichever backend is used. RDF backends additionally expose
    sparql_select / sparql_construct; the property-graph backends (Neo4j, relate)
    expose cypher_query, shortest_path and neighbourhood instead, since an LPG does
    not answer SPARQL.
    """
    from ontorag.mcp_backend import LocalRdfBackend, RemoteSparqlBackend
    from ontorag.mcp_server import create_mcp_app, serve

    if relate:
        from ontorag import relate_store as rs
        try:
            path = rs.resolve(relate)
            backend = rs.RelateBackend(path)
        except rs.RelateStoreError as e:
            raise typer.BadParameter(str(e))
        _log.info("MCP server: relate backend at %s", path)
    elif neo4j:
        from ontorag.neo4j_store import Neo4jBackend
        _log.info("MCP server: neo4j backend")
        backend = Neo4jBackend(uri=uri, user=user, password=password, database=database)
    elif sparql_endpoint:
        _log.info("MCP server: remote backend at %s", sparql_endpoint)
        backend = RemoteSparqlBackend(sparql_endpoint)
    else:
        if not onto or not inst:
            raise typer.BadParameter(
                "Provide --relate, OR --neo4j, OR --sparql-endpoint, OR both --onto and --inst")
        _log.info("MCP server: local backend onto=%s inst=%s", onto, inst)
        backend = LocalRdfBackend(onto, inst)

    serve(create_mcp_app(backend), host, port)


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
    from ontorag.mcp_server import serve

    serve(create_ontology_mcp(catalog), host, port)


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
    mcp_url: Optional[str] = typer.Option(None, "--mcp-url", help="REST API base URL for remote baselines (default: ONTORAG_MCP_URL or https://mcp.rpg-schema.org/mcp)"),
):
    """
    Create an initial schema card by composing one or more baseline ontologies.

    Baselines are resolved first from the local catalog, then from the remote
    REST API at ONTORAG_MCP_URL (default: https://mcp.rpg-schema.org/mcp; the /mcp suffix is optional).

    Each class/property carries an 'origin' field tracking its source.
    """
    from ontorag.ontology_catalog import compose_baselines

    slugs = [s.strip() for s in baselines.split(",") if s.strip()]
    if not slugs:
        raise typer.BadParameter("Provide at least one baseline slug.")

    _log.info("Composing baselines: %s", slugs)
    card = compose_baselines(catalog, slugs, target_namespace=namespace, mcp_url=mcp_url)
    write_json(out, card)

    cls_count = len(card.get("classes", []))
    dp_count = len(card.get("datatype_properties", []))
    op_count = len(card.get("object_properties", []))
    typer.echo(
        f"OK init-schema-card: baselines={slugs} "
        f"classes={cls_count} datatype_props={dp_count} object_props={op_count} out={out}"
    )


@app.command("doctor")
def cmd_doctor():
    """Report what this local environment can do: ingestion engines + LLM provider."""
    import importlib.util as _u
    from ontorag.extractor_ingest import engine_status

    typer.echo("OntoRAG environment\n\nIngestion engines (choose with --engine / ONTORAG_INGEST_ENGINE):")
    for name, ok, hint in engine_status():
        typer.echo(f"  {'OK ' if ok else '-- '} {name:13}{'' if ok else '→ pip install ' + hint}")
    have_pdf = _u.find_spec("fitz") is not None
    pdf_hint = "" if have_pdf else "→ pip install 'ontorag[pdf]' (PyMuPDF)"
    typer.echo(f"  {'OK ' if have_pdf else '-- '} {'builtin-pdf':13}{pdf_hint}")

    from ontorag import vector_index as vi
    try:
        ext = vi.extension_path()
        vec_line = f"OK  sqlite-vector {vi.EXTENSION_VERSION} at {ext}"
    except vi.VectorIndexError as e:
        vec_line = f"--  sqlite-vector    → {e}"
    typer.echo(f"\nVector index (`ontorag index`):\n  {vec_line}")

    from ontorag.relate_store import relate_version
    rv = relate_version()
    graph_line = (f"OK  relate {rv}" if rv else "--  relate           → pip install 'ontorag[relate]'")
    typer.echo(f"\nGraph index (`ontorag index-graph`, `mcp-server --relate`):\n  {graph_line}")

    key_llm = bool(llm_config.api_key())
    typer.echo("\nLLM (extract-schema / extract-instances / align-schema):")
    typer.echo(f"  {'OK ' if key_llm else '-- '} api_key {'set' if key_llm else 'missing'}"
               f"    model={llm_config.model()}    base_url={llm_config.base_url()}")
    typer.echo(
        "\nOverride any LLM setting per-invocation with global flags before the "
        "subcommand, e.g.:\n"
        "  ontorag --model <m> --api-key <k> --base-url <url> extract-schema ...\n"
        "Precedence: CLI flag > environment variable > default. Point --base-url at a "
        "local OpenAI-compatible server (e.g. ollama: http://localhost:11434/v1) to run offline.\n"
        "\nMinimal local run: `ontorag ingest file.md` needs nothing; extraction needs an LLM."
    )


# -------------------------
# Dataset format
# -------------------------

@app.command("build-dataset")
def cmd_build_dataset(
    dataset_dir: str = typer.Argument(".", help="Dataset directory (contains ontology/ and content/)."),
    graph: str = typer.Option("ontology/world.ttl", "--graph", help="Path (relative to the dataset dir) to the world/instance graph."),
    name: Optional[str] = typer.Option(None, "--name", help="Dataset title (default: kept from the manifest, else the directory name)."),
    license: Optional[str] = typer.Option(None, "--license", help="SPDX licence of the content, e.g. CC-BY-SA-4.0."),
    base_iri: Optional[str] = typer.Option(None, "--base-iri", help="Base IRI (default: kept from the manifest, else inferred from the graph)."),
    version: Optional[str] = typer.Option(None, "--version", help="Dataset content version (default: kept, else 0.1.0)."),
):
    """
    Complete a dataset directory into the OntoRAG dataset format 0.1
    (https://ontorag.org/vocab/#format), so ontorag-mcp and other consumers can
    serve it: per-source chunk files, the entity index, the source and pack
    registries, and the required manifest fields. Existing manifest keys are kept.
    """
    from ontorag.dataset_package import complete_dataset

    try:
        m = complete_dataset(dataset_dir, graph_rel=graph, name=name, license=license,
                             base_iri=base_iri, version=version)
    except RuntimeError as e:
        typer.secho(f"ERROR build-dataset: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    c = m["content"]["counts"]
    typer.echo(f"dataset {m['dataset']['id']} {m['dataset']['version']}: "
               f"{m['ontology']['counts']['entities']} entities, {c['chunks']} chunks, "
               f"{c['documents']} source(s)")


# -------------------------
# Hub (publish to GitHub)
# -------------------------

hub_app = typer.Typer(add_completion=False,
                      help="Publish a locally-built dataset to GitHub for the OntoRAG Hub.")
app.add_typer(hub_app, name="hub")


@hub_app.command("push")
def cmd_hub_push(
    dataset_dir: str = typer.Argument(".", help="Local dataset directory (contains ontology/ and, optionally, content/)."),
    repo: str = typer.Option(..., "--repo", "-r", help="Target GitHub repo as 'owner/name' (owner defaults to your account if just 'name')."),
    token: Optional[str] = typer.Option(None, "--token", help="GitHub token with 'repo' scope (else GITHUB_TOKEN / GH_TOKEN)."),
    include_sources: bool = typer.Option(True, "--include-sources/--no-include-sources",
                                help="Upload the original corpus under content/sources/. Use --no-include-sources to publish only the derived ontology + graph."),
    private: bool = typer.Option(True, "--private/--public", help="Visibility when the repo is created (default private)."),
    name: Optional[str] = typer.Option(None, "--name", help="Dataset title for a generated manifest (default: repo name)."),
    license: str = typer.Option("", "--license", help="License for a generated manifest (e.g. CC-BY-SA-4.0)."),
    base_iri: Optional[str] = typer.Option(None, "--base-iri", help="Base IRI for a generated manifest (default: inferred from the graph)."),
    graph: str = typer.Option("ontology/world.ttl", "--graph", help="Path (relative to the dataset dir) to the world/instance graph."),
    regenerate_manifest: bool = typer.Option(False, "--regenerate-manifest", help="Rewrite manifest.json even if one already exists."),
    message: Optional[str] = typer.Option(None, "--message", "-M", help="Commit message."),
):
    """
    Publish a locally-built dataset to GitHub so the Hub can explore or fork it.

    Unless the directory already has a complete manifest, first completes it into
    the OntoRAG dataset format (as `ontorag build-dataset` does), then commits the
    dataset via the GitHub API. Choose whether to
    upload the raw corpus (--include-sources, default) or only the derived
    ontology + graph (--no-include-sources).
    """
    from ontorag.hub_push import push_dataset

    try:
        res = push_dataset(dataset_dir, repo, token=token, include_sources=include_sources,
                           private=private, name=name, license=license, base_iri=base_iri,
                           graph=graph, message=message, regen_manifest=regenerate_manifest)
    except RuntimeError as e:
        typer.secho(f"ERROR hub push: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    typer.echo(
        f"OK hub push: {res['url']} "
        f"({res['files']} files, commit {res['commit'][:7]}, "
        f"{'created' if res['created'] else 'updated'}, "
        f"corpus {'included' if res['include_sources'] else 'excluded'})")
    typer.echo("   It now respects the ontorag structure — explore or fork it from the Hub.")


def main():
    app()


if __name__ == "__main__":
    main()
