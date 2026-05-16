# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
uv sync          # installs core deps; rebuilds .venv if broken
cp .example.env .env   # fill in OPENROUTER_API_KEY and optionally BLAZEGRAPH_ENDPOINT
```

Requires Python 3.12. Uses `uv` (see `uv.lock`). `pymupdf`, `pageindex`, and `llama-index` are optional extras:

```bash
uv sync --extra pdf        # for PDF ingest via PyMuPDF
uv sync --extra pageindex  # for hierarchical PDF ingest via PageIndex
uv sync --extra llamaindex # for LlamaIndex fixed-chunk ingest
```

## CLI

The shell `PYTHONPATH` on this server is polluted with old system Python paths. Always invoke as:

```bash
PYTHONPATH=/srv/sembase/extractor_new uv run ontorag <command>
```

Verbosity flags go before the subcommand: `uv run ontorag -v <command>` or `-vv` for debug traces.

## Key commands

| Command | Purpose |
|---|---|
| `ontorag ingest <file> --out data/dto` | Parse document → DocumentDTO + ChunkDTOs (content-addressed, skips re-runs) |
| `ontorag extract-schema --chunks ... --schema-card ... --out ...` | LLM per-chunk ontology proposals → aggregated proposal JSON |
| `ontorag align-schema --proposal ... --baseline ... --out ...` | LLM alignment of induced items against baseline ontologies |
| `ontorag build-schema-card --previous ... --proposal ... --out ...` | Deterministic merge of proposal into schema card |
| `ontorag export-schema-ttl --proposal ... --out ... --namespace ...` | Proposal/alignment JSON → OWL/RDFS Turtle |
| `ontorag extract-instances --chunks ... --schema-card ... --out-ttl ...` | LLM instance extraction → RDF TTL with PROV provenance |
| `ontorag sparql-server --onto ... --inst ...` | FastAPI in-memory SPARQL endpoint (port 8890) |
| `ontorag mcp-server --onto ... --inst ...` | Knowledge graph MCP server (port 9010) |
| `ontorag ontology-mcp --catalog ...` | Ontology catalog MCP server (port 9020) |
| `ontorag register-ontology <slug> <ttl>` | Register a baseline OWL/TTL into the catalog |
| `ontorag init-schema-card --baselines foaf,prov --out ...` | Compose baselines → initial schema card |
| `ontorag hub` | Start Hub API server (requires GitHub OAuth env vars) |

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
| `cli.py` | Typer CLI — all 13 commands |
| `dto.py` | `DocumentDTO`, `ChunkDTO`, `ProvenanceDTO`; content-hash (`stable_document_id`) |
| `extractor_ingest.py` | Document parsing via PageIndex (hierarchical) or LlamaIndex (fixed chunks) |
| `storage_jsonl.py` | JSONL persistence for DTOs |
| `ontology_extractor_openrouter.py` | LLM schema proposal extraction (per chunk) |
| `instance_extractor_openrouter.py` | LLM instance extraction (per chunk) |
| `proposal_aggregator.py` | Merge per-chunk proposals into one document-level proposal |
| `schema_card.py` | Deterministic schema card merge with origin tracking |
| `schema_alignment.py` | LLM-based alignment of induced items against baselines |
| `proposal_to_ttl.py` | Schema proposal/alignment JSON → rdflib `Graph` (OWL/RDFS) |
| `instances_to_ttl.py` | Instance proposals → rdflib `Graph` with PROV mention nodes |
| `blazegraph.py` | Blazegraph REST API (upload TTL, SPARQL UPDATE) |
| `sparql_server.py` | FastAPI SPARQL endpoint (SELECT/ASK/CONSTRUCT/DESCRIBE, content negotiation) |
| `mcp_backend.py` | `SparqlBackend` ABC + `LocalRdfBackend` + `RemoteSparqlBackend` |
| `mcp_server.py` | Knowledge graph MCP tools (`sparql_select`, `describe`, `list_by_class`, etc.) |
| `mcp_client.py` | Async SSE client for remote MCP |
| `ontology_catalog.py` | Local catalog + OWL/TTL → schema card converter; remote baseline fetch |
| `ontology_mcp.py` | Ontology catalog MCP server |
| `verbosity.py` | Logging setup (`-v`/`-vv` flags) |
| `hub/` | Hub FastAPI app, GitHub OAuth, GitHub storage backend |

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

All LLM calls go through OpenRouter (`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`). The modules `ontology_extractor_openrouter.py`, `instance_extractor_openrouter.py`, and `schema_alignment.py` each manage their own `requests` calls directly (no shared client abstraction). The `extract-schema` command adds a 10-second inter-chunk delay; `extract-instances` does not.

`align-schema` supports **partial-save and auto-resume**: if the output file exists with `"_partial": true`, it resumes from the last completed category.

### Ingest engines

`ontorag ingest` supports two engines via `--engine`:
- `llamaindex` (default): fixed-size chunks (1024 tokens, 120 overlap)
- `pageindex`: hierarchical section detection, requires `PAGEINDEX_API_KEY`

Documents are content-hashed (SHA-256); re-ingesting the same file is a no-op unless `--force` is passed.

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

| Variable | Required for |
|---|---|
| `OPENROUTER_API_KEY` | All LLM commands |
| `OPENROUTER_MODEL` | LLM model selection (default: `openai/gpt-4o-mini`) |
| `OPENROUTER_BASE_URL` | OpenRouter endpoint |
| `BLAZEGRAPH_ENDPOINT` | `load-ttl`, `sparql-update` commands |
| `PAGEINDEX_API_KEY` | `ingest --engine pageindex` |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` / `HUB_JWT_SECRET` | `hub` command |
| `ONTORAG_MCP_URL` | Remote baseline resolution in `init-schema-card` (default: `https://mcp.rpg-schema.org`) |

## Known issues

- `blazegraph.py`: raw TTL is string-interpolated into SPARQL UPDATE — breaks for non-trivial TTL; Blazegraph REST bulk load is the proper fix.
- Hub async endpoints block the event loop (sync LLM calls inside `async def`).
- No test suite exists in this repository.
