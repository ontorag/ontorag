# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
uv sync          # installs core deps; rebuilds .venv if broken
cp .example.env .env   # fill in OPENROUTER_API_KEY and optionally BLAZEGRAPH_ENDPOINT
```

Requires Python 3.12. Uses `uv` (see `uv.lock`). The `builtin` ingest engine is the default and needs no extras for Markdown/text/HTML/EPUB; parsers are optional extras:

```bash
uv sync --extra pdf          # PDF ingest via PyMuPDF (builtin engine)
uv sync --extra pageindex    # hierarchical PDF via hosted PageIndex API
uv sync --extra llamaindex   # LlamaIndex fixed-chunk ingest
uv sync --extra docling      # IBM Docling, layout-aware PDF/DOCX/PPTX
uv sync --extra unstructured # Unstructured typed elements
uv sync --extra neo4j        # Neo4j export (official bolt driver)
uv sync --extra relate       # embedded Cypher graph store (index-graph, mcp-server --relate)
```

## CLI

The shell `PYTHONPATH` on this server is polluted with old system Python paths. Always invoke as:

```bash
PYTHONPATH=/srv/sembase/extractor_new uv run ontorag <command>
```

Verbosity flags go before the subcommand: `uv run ontorag -v <command>` or `-vv` for debug traces.

**LLM settings are global flags (before the subcommand)**, resolved by `llm_config.py` with precedence **CLI flag > env var > default**: `--model`/`-m` (`OPENROUTER_MODEL`), `--api-key` (`OPENROUTER_API_KEY`), `--base-url` (`OPENROUTER_BASE_URL`; point at a local ollama), `--app-name`, `--site-url`. E.g. `ontorag --model '~deepseek/deepseek-v4-flash-latest' --api-key sk-... extract-schema ...`. They apply to `extract-schema`, `align-schema`, `extract-instances`; `doctor` prints the effective values.

**Execution flags (also global):** `--concurrency`/`-j` (`ONTORAG_CONCURRENCY`, default 4) runs chunk LLM calls in parallel (`parallel.py`, `map_chunks`) — the main speed lever, since per-chunk latency dominates; `-j 1` = sequential. `--slim-card` (`ONTORAG_SLIM_CARD`, default off) prunes the card to chunk-relevant terms via `card_slim.py` (cheaper prompts, but can lower instance recall — opt-in).

## Key commands

| Command | Purpose |
|---|---|
| `ontorag ingest <file> --out data/dto [--engine builtin]` | Parse document → DocumentDTO + ChunkDTOs (content-addressed, skips re-runs) |
| `ontorag doctor` | Report available ingest engines + active LLM provider/model |
| `ontorag extract-schema --chunks ... --schema-card ... --out ...` | LLM per-chunk ontology proposals → aggregated proposal JSON |
| `ontorag align-schema --proposal ... --baseline ... --out ...` | LLM alignment of induced items against baseline ontologies |
| `ontorag build-schema-card --previous ... --proposal ... --out ...` | Deterministic merge of proposal into schema card |
| `ontorag export-schema-ttl --proposal ... --out ... --namespace ...` | Proposal/alignment JSON → OWL/RDFS Turtle |
| `ontorag extract-instances --chunks ... --schema-card ... --out-ttl ...` | LLM instance extraction → RDF TTL with PROV provenance |
| `ontorag load-neo4j --file ontology/world.ttl [--dry-run]` | Project an instances TTL into Neo4j as a native property graph (idempotent; needs `[neo4j]`) |
| `ontorag index <dir> [--ship] [--quantize turbo4]` | Build the query form of a dataset's vectors: sqlite-vector DB from the JSONL shards. A cache (`.ontorag/vectors.db`, gitignored), or `--ship` for a 9-33 MB publishable index |
| `ontorag index-graph <dir> [--force]` | Build the query form of a dataset's graph: `world.ttl` -> one embedded relate file (`.ontorag/graph.rl`, gitignored cache, stamped). Never commit it (binary, ~3x the TTL) |
| `ontorag sparql-server --onto ... --inst ...` | FastAPI in-memory SPARQL endpoint (port 8890) |
| `ontorag mcp-server --onto ... --inst ...` | Knowledge graph MCP server (port 9010); also `--sparql-endpoint <url>`, `--neo4j`, or `--relate <dataset dir or .rl>` (fastest startup; adds graph search) |
| `ontorag ontology-mcp --catalog ...` | Ontology catalog MCP server (port 9020) |
| `ontorag register-ontology <slug> <ttl>` | Register a baseline OWL/TTL into the catalog |
| `ontorag init-schema-card --baselines foaf,prov --out ...` | Compose baselines → initial schema card |
| `ontorag run-stage <propose\|extract> [--root DIR] [--full]` | Run one governed pipeline stage over a dataset dir — the single definition of stage composition, driven by Actions, the Hub and the desktop client alike |
| `ontorag hub push <dir> --repo owner/name [--no-include-sources]` | Publish a built dataset to GitHub for the Hub (synth manifest.json + Git Data API commit) |

## Architecture

The core philosophy: **LLMs propose. Code decides. Humans govern.**

Pipeline flow:
```
Baseline Ontologies (TTL)
    → init-schema-card  → schema_card.json
Documents
    → ingest            → data/dto/documents/*.json + data/dto/chunks/*.jsonl
    → extract-schema    → data/proposals/*.json         (LLM, per-chunk → aggregated)
    → align-schema      → alignment JSON                (LLM, optional baseline alignment)
    → build-schema-card → schema_card.json              (deterministic merge)
    → export-schema-ttl → staging_schema.ttl
    → extract-instances → instances.ttl                 (LLM, RDF + PROV provenance)
    → sparql-server / mcp-server
```

### Module map (`ontorag/`)

| File | Role |
|---|---|
| `cli.py` | Typer CLI — all 16 commands (incl. `doctor`, `hub push`) |
| `run_stage.py` | Stage composition (`propose`, `extract`) **and incremental extraction**: `ontology/extraction_state.json` records which documents each LLM step has seen, so adding a file to a large corpus only pays for that file. Schema induction keeps per-chunk proposals (`ontology/proposals.raw.jsonl`) and re-aggregates rather than merging aggregates; instances union into `world.ttl`. A changed schema card forces a full instance pass. Editing a file re-extracts it but does not retract its previous contribution — `--full` after edits. Also: which CLI commands run, in what order, with which manifest-derived LLM flags. Previously copied into every dataset repo as `.hub/run_stage.py`, which drifted and never reached already-provisioned repos. A frozen build re-invokes `sys.executable`, so a packaged app runs stages with no Python installed |
| `hub_push.py` | Publish a dataset to GitHub for the Hub: synth Hub-compatible `manifest.json` (base IRI + counts from the graph) + single-commit via the Git Data API; `--include-sources` toggles the raw corpus |
| `dto.py` | `DocumentDTO`, `ChunkDTO`, `ProvenanceDTO`; content-hash (`stable_document_id`) |
| `extractor_ingest.py` | Pluggable ingest engines (`ENGINES` registry): builtin (default), pageindex, llamaindex, docling, unstructured; `engine_status()` powers `doctor` |
| `storage_jsonl.py` | JSONL persistence for DTOs |
| `ontology_extractor_openrouter.py` | LLM schema proposal extraction (per chunk) |
| `instance_extractor_openrouter.py` | LLM instance extraction (per chunk) |
| `proposal_aggregator.py` | Merge per-chunk proposals into one document-level proposal |
| `schema_card.py` | Deterministic schema card merge with origin tracking |
| `schema_alignment.py` | LLM-based alignment of induced items against baselines |
| `proposal_to_ttl.py` | Schema proposal/alignment JSON → rdflib `Graph` (OWL/RDFS) |
| `instances_to_ttl.py` | Instance proposals → rdflib `Graph` with PROV mention nodes |
| `blazegraph.py` | Blazegraph REST API (upload TTL, SPARQL UPDATE) |
| `neo4j_store.py` | Neo4j sink: RDF -> labelled property graph. `graph_to_rows()` is a pure, driver-free mapping (offline-testable); `write_rows(run, rows)` is the store-agnostic batched MERGE writer (shared with relate), `load_rows()` the Neo4j I/O; `CypherBackend` holds every Cypher query (navigation + `shortest_path`/`neighbourhood` + `cypher`) and subclasses only implement a read-only `_read` — `Neo4jBackend` via read transactions. Neo4j is a **parallel serving target, not a `SparqlBackend`** — it is an LPG and does not answer SPARQL. The projection stores `propertyIris` on nodes and mentions so predicate IRIs survive the round trip |
| `relate_store.py` | Graph as a query form: instances TTL -> one embedded [relate](https://relatedb.ai) `.rl` file (in-process Cypher), built with the Neo4j projection (`graph_to_rows` + `write_rows`), stamped like `vector_index`, written atomically. **The temp file must end in `.rl`** — relate picks single-file vs directory layout from the suffix. `RelateBackend` opens it `read_only=True` (engine refuses writes) on one dedicated thread, because relate connections are thread-affine and MCP tools run on worker threads. Not used for vectors: its vector index is exact brute force (395 MB / ~150 ms on 18k x 768) |
| `vector_index.py` | Vectors as a query form: JSONL shards -> one SQLite file searched by [sqlite-vector](https://github.com/sqliteai/sqlite-vector). The shards stay the committed, incremental source; this is derived and disposable, stamped (sha256 of config + shard size/mtime) so an unchanged dataset is not rebuilt. `--ship` drops the full-precision column and keeps the quantised index -- 9 MB against 144 MB of shards, still answering -- for attaching to a release. The extension is a pinned binary, fetched on demand: there is no PyPI package (the `sqlite-vector` name there is an unrelated project) |
| `sparql_server.py` | FastAPI SPARQL endpoint (SELECT/ASK/CONSTRUCT/DESCRIBE, content negotiation) |
| `mcp_backend.py` | `GraphBackend` ABC (`describe`/`list_by_class`/`outgoing`/`incoming`/`mentions`) + `SparqlBackend` (adds `select`/`construct`, and implements the four in SPARQL) + `LocalRdfBackend` + `RemoteSparqlBackend`. All navigation returns SPARQL Results JSON, whatever the store |
| `mcp_server.py` | Knowledge graph MCP tools. Thin: the five navigation tools (incl. `mentions`, the citation primitive) delegate to the backend; query-language tools are registered by capability — `sparql_select`/`sparql_construct` for `SparqlBackend`, `cypher_query` + `shortest_path`/`neighbourhood` for the property-graph backends (relate, Neo4j) — so a client never sees a tool that must fail |
| `mcp_client.py` | Async SSE client for remote MCP |
| `ontology_catalog.py` | Local catalog + OWL/TTL → schema card converter; remote baseline fetch |
| `ontology_mcp.py` | Ontology catalog MCP server |
| `llm_config.py` | OpenRouter settings resolver (CLI flag > env > default); `set_overrides()` called by the root callback |
| `verbosity.py` | Logging setup (`-v`/`-vv` flags) |

### Schema card format

The schema card (`schema_card.json`) is the central governance artifact:

```json
{
  "version": "<ISO timestamp>",
  "namespace": "http://my.org/ns/",
  "classes": [{"name": "...", "description": "...", "origin": "foaf|schema_org|induced|..."}],
  "datatype_properties": [{"name": "...", "domain": "...", "range": "string|integer|...", "description": "...", "origin": "..."}],
  "object_properties": [{"name": "...", "domain": "...", "range": "...", "description": "...", "origin": "..."}],
  "events": [],
  "aliases": [{"names": [...], "rationale": "..."}],
  "warnings": []
}
```

Dedup is by normalized (lowercased) name. Baseline origins are preserved across merges; LLM-induced items get `"origin": "induced"`.

### LLM integration

All LLM calls go through OpenRouter. The three modules `ontology_extractor_openrouter.py`, `instance_extractor_openrouter.py`, and `schema_alignment.py` each issue their own `requests` calls, but resolve settings through `llm_config.py` (`api_key()`/`model()`/`base_url()`/`app_name()`/`site_url()`) **at call time** — so global CLI flags (`--model`, `--api-key`, `--base-url`, …) registered by the root callback via `llm_config.set_overrides()` are honoured. `extract-schema` and `extract-instances` process chunks **concurrently** via `parallel.map_chunks` (`--concurrency`/`-j`, default 4) with per-chunk retry/backoff on failure; there is no fixed inter-chunk delay.

`align-schema` supports **partial-save and auto-resume**: if the output file exists with `"_partial": true`, it resumes from the last completed category.

### Ingest engines

`ontorag ingest` uses a **pluggable engine registry** (`extractor_ingest.py`, `ENGINES` dict) selected via `--engine` / `ONTORAG_INGEST_ENGINE`:
- `builtin` (**default**): no deps/keys — Markdown/text via local recursive splitter, EPUB/HTML via `ebooklib`+`html2text`, PDF via PyMuPDF (`[pdf]` extra)
- `pageindex`: hosted hierarchical PDF section detection, requires `PAGEINDEX_API_KEY` (`[pageindex]` extra)
- `llamaindex`: fixed-size chunks, 1024 tokens / 120 overlap (`[llamaindex]` extra)
- `docling`: IBM Docling, layout-aware PDF/DOCX/PPTX → Markdown (`[docling]` extra)
- `unstructured`: typed elements (`[unstructured]` extra)

Optional engines are lazy-imported and fail with a friendly `pip install 'ontorag[<extra>]'` message if missing. `ontorag doctor` reports which engines are installed plus the active LLM provider. Documents are content-hashed (SHA-256); re-ingesting the same file is a no-op unless `--force` is passed.

### Data directories

```
data/dto/documents/    DocumentDTO JSON files (doc_<hash>.json)
data/dto/chunks/       ChunkDTO JSONL files (doc_<hash>.jsonl)
data/proposals/        Aggregated schema proposals and alignment JSON
data/schema/           Schema cards and exported TTL
data/instances/        Instance RDF TTL
data/ontologies/       Baseline catalog (catalog.json + *.ttl)
data/ttl/              Misc TTL files
```

## Environment variables

All LLM variables below are also settable as global CLI flags (flag > env > default) — see the CLI section.

| Variable | CLI flag | Required for |
|---|---|---|
| `OPENROUTER_API_KEY` | `--api-key` | All LLM commands |
| `OPENROUTER_MODEL` | `--model`/`-m` | LLM model (recommended: `~deepseek/deepseek-v4-flash-latest`; avoid `*:free` slugs for real runs) |
| `OPENROUTER_BASE_URL` | `--base-url` | OpenRouter endpoint (point at a local ollama to run offline) |
| `OPENROUTER_APP_NAME` / `OPENROUTER_SITE_URL` | `--app-name` / `--site-url` | OpenRouter `X-Title` / `HTTP-Referer` headers |
| `BLAZEGRAPH_ENDPOINT` | — | `load-ttl`, `sparql-update` commands |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` | `--uri` / `--user` / `--password` / `--database` | `load-neo4j` (defaults: `bolt://localhost:7687`, `neo4j`, no password, server default DB) |
| `PAGEINDEX_API_KEY` | — | `ingest --engine pageindex` |
| `ONTORAG_MCP_URL` | `--mcp-url` | Remote baseline resolution in `init-schema-card` (default: `https://mcp.rpg-schema.org`) |

## Known issues

- `blazegraph.py`: raw TTL is string-interpolated into SPARQL UPDATE — breaks for non-trivial TTL; Blazegraph REST bulk load is the proper fix.
- Extraction speed is latency-bound, not prompt-size-bound (slimming a 45KB card to 4KB gave ~no wall-clock change): the fix is concurrency (`-j`, default 4). `--slim-card` remains an opt-in token-cost saver (can lower instance recall).
- Tests: offline suites in `tests/` (CLI surface, builtin ingest, engine errors, alignment normalization, baseline-IRI export, card slimming, parallel map_chunks); run in Docker py3.12/3.13, not the polluted `.venv`.
