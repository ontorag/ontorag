# OntoRAG

**OntoRAG** is an ontology-first alternative to traditional Retrieval-Augmented Generation (RAG).

Instead of retrieving text fragments and hoping the LLM reasons correctly, OntoRAG:
- extracts **explicit structure** from documents,
- builds a governed **knowledge graph** (RDF),
- and uses LLMs only where they add value: proposal, extraction, interpretation.

The result is a system that is inspectable, auditable, evolvable, and usable beyond chat.

---

## Why OntoRAG exists

Traditional RAG systems suffer from structural weaknesses:

- No explicit domain model
- No traceability from answers to sources
- No governance or evolution of knowledge
- Hidden schema inside prompts and embeddings

OntoRAG flips the model:

> **Baselines --> Documents --> DTOs --> Ontology --> Instances --> SPARQL --> MCP tools --> LLM reasoning**

LLMs *propose*.
Code *decides*.
Humans *govern*.

---

## Architecture overview

```
Baseline Ontologies (OWL/TTL)
|
+-- Ontology Catalog (register, browse, compose)
|
v
Schema Card (initial or evolved)
|
Documents --> DTOs (Document / Chunk)
                |
                +-- Ontology Extraction (LLM -> proposals)
                |     |
                |     v
                +-- Schema Card (deterministic merge, origin-tracked)
                |
                +-- Instance Extraction (LLM -> RDF with provenance)
                |
                v
           Knowledge Graph (TTL / SPARQL)
                |
                +-- SPARQL endpoint (local rdflib or Blazegraph)
                +-- Knowledge MCP Server (graph tools for agents)
                +-- Ontology MCP Server (catalog tools for agents)
```

---

## Core concepts

### 1. Ontology catalog and baselines

Before processing any documents, you can seed OntoRAG with **baseline ontologies** -- existing OWL/RDFS vocabularies (FOAF, Schema.org, PROV-O, domain-specific schemas, etc.).

Baselines are registered in a **catalog** (a directory of TTL files with a JSON manifest). You can:
- register standard or custom ontologies,
- browse and search across all baselines,
- compose multiple baselines into an initial schema card.

Each class and property from a baseline carries an **`origin`** field (e.g., `"foaf"`, `"schema_org"`) so you always know where a term came from.

### 2. DTO-first ingestion

Documents are parsed using best-in-class loaders (via LlamaIndex) into stable **DocumentDTO / ChunkDTO** objects.

DTOs are:
- format-agnostic (PDF, Markdown, CSV, DOCX, HTML, EPUB, ...),
- persistent (stored as JSON + JSONL),
- replayable,
- provenance-aware (page, section, text snippet, source path).

They are the semantic checkpoint of the pipeline.

### 3. Ontology induction (proposal, not truth)

LLMs analyze DTO chunks and propose:
- candidate classes,
- datatype properties,
- object properties,
- events,
- merge/alias suggestions.

These are **proposals**, not production schema. The LLM sees the current schema card and is instructed to reuse existing terms before inventing new ones.

### 4. Schema Card

The **Schema Card** is a compact, deterministic JSON description of the current ontology:

```json
{
  "version": "2026-02-12T10:00:00Z",
  "namespace": "http://my.org/ns/",
  "classes": [
    {"name": "Person", "description": "A human being.", "origin": "foaf"},
    {"name": "Invoice", "description": "A commercial invoice.", "origin": "induced"}
  ],
  "datatype_properties": [
    {"name": "email", "domain": "Person", "range": "string", "description": "...", "origin": "foaf"}
  ],
  "object_properties": [
    {"name": "knows", "domain": "Person", "range": "Person", "description": "...", "origin": "foaf"}
  ],
  "events": [],
  "aliases": [
    {"names": ["Person", "Agent"], "rationale": "FOAF uses both interchangeably"}
  ],
  "warnings": []
}
```

It is:
- versioned (ISO timestamp),
- human-reviewable,
- origin-tracked (`"foaf"`, `"schema_org"`, `"induced"`, etc.),
- used to guide all downstream extraction.

The merge is **deterministic**: classes and properties are deduplicated by normalized name, descriptions are merged (longer wins), and baseline origins are preserved.

### 5. Instance extraction with provenance

Given a stable schema card, OntoRAG extracts **instances** from documents:

- RDF instances typed to schema card classes
- datatype properties as literals
- object properties linking instances
- every fact linked to its source chunk via PROV-style mention nodes (quote, page, section)

No hallucinated facts, no orphan triples.

### 6. Knowledge graph backends

OntoRAG supports two modes:

- **Local inspection**: in-memory RDF via rdflib, served as a FastAPI SPARQL endpoint
- **Production-grade**: external SPARQL engines (Blazegraph, QLever, others)

Both are exposed via standard SPARQL (GET/POST `/sparql`).

### 7. MCP integration

OntoRAG provides **two MCP servers**:

**Knowledge MCP** (default port 9010) -- query the knowledge graph:
- `sparql_select` / `sparql_construct` -- raw SPARQL queries
- `describe` -- describe a resource by IRI
- `list_by_class` -- find instances of a class
- `outgoing` / `incoming` -- graph traversal

**Ontology Catalog MCP** (default port 9020) -- browse and compose baselines:
- `list_ontologies` -- list registered baselines
- `inspect_ontology` -- view classes/properties of a baseline
- `search_classes` / `search_properties` -- search across all baselines
- `compose` -- merge selected baselines into a schema card
- `add_ontology` -- register a new baseline from TTL content

This allows LLM agents to both select their starting ontology and query the resulting knowledge graph.

---

## Installation

```bash
pip install -e .
```

Core dependencies (declared in `pyproject.toml`):
`typer`, `requests`, `pydantic`, `rdflib`, `llama-index`, `python-dotenv`, `fastapi`, `uvicorn`, `fastmcp`, `EbookLib`, `html2text`.

---

## Configuration

Copy the example environment file and fill in your API key:

```bash
cp .example.env .env
```

```env
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_APP_NAME=OntoRAG
OPENROUTER_SITE_URL=https://ontorag.github.io

# Optional: only needed for load-ttl / sparql-update commands
BLAZEGRAPH_ENDPOINT=http://localhost:9999/blazegraph/namespace/ontorag/sparql
```

---

## CLI reference

All commands are available via `ontorag <command> --help`.

### Ontology catalog commands

**Register a baseline ontology:**

```bash
ontorag register-ontology foaf ./ontologies/foaf.ttl \
  --label "Friend of a Friend" \
  --description "People, social networks, and their connections" \
  --tags "social,people"
```

Copies the TTL file into the catalog directory, auto-detects the namespace, and registers it in `catalog.json`.

**Create an initial schema card from baselines:**

```bash
ontorag init-schema-card \
  --baselines foaf,prov \
  --out data/schema/schema_card.json \
  --namespace http://my.org/ns/
```

Parses the selected OWL/TTL baselines, extracts classes and properties, and merges them into a single schema card with `origin` tracking.

**Start the ontology catalog MCP server:**

```bash
ontorag ontology-mcp --catalog ./data/ontologies --port 9020
```

### Document processing commands

**Ingest a document:**

```bash
ontorag ingest data/raw/manual.pdf --out data/dto
ontorag ingest data/raw/handbook.epub --out data/dto
```

Parses the file via LlamaIndex, splits into chunks (1024 tokens, 120 overlap), and stores DocumentDTO + ChunkDTOs as JSON + JSONL. Supported formats include PDF, DOCX, Markdown, HTML, CSV, EPUB, and more.

**Extract ontology proposals:**

```bash
ontorag extract-schema \
  --chunks data/dto/chunks/doc_x.jsonl \
  --schema-card data/schema/schema_card.json \
  --out data/proposals/doc_x.schema.json
```

Sends each chunk + the current schema card to the LLM. The LLM proposes new classes, properties, events, and merge suggestions. Per-chunk proposals are aggregated into a single document-level proposal.

**Build schema card (deterministic merge):**

```bash
ontorag build-schema-card \
  --previous data/schema/schema_card.json \
  --proposal data/proposals/doc_x.schema.json \
  --out data/schema/schema_card.next.json
```

Deterministically merges the proposal into the existing schema card. Deduplicates by normalized name, normalizes datatype ranges, validates domain/range references, and accumulates aliases and warnings. New items get `"origin": "induced"`.

**Export schema to Turtle:**

```bash
ontorag export-schema-ttl \
  --proposal data/proposals/doc_x.schema.json \
  --out data/schema/staging_schema.ttl \
  --namespace http://my.org/ns/
```

**Extract instances:**

```bash
ontorag extract-instances \
  --chunks data/dto/chunks/doc_x.jsonl \
  --schema-card data/schema/schema_card.json \
  --out-ttl data/instances/doc_x.instances.ttl
```

Extracts structured instances constrained to the schema card, then converts to RDF with PROV-style provenance (quote, page, section for every fact).

### Knowledge graph commands

**Upload TTL to Blazegraph:**

```bash
ontorag load-ttl \
  --file data/schema/staging_schema.ttl \
  --graph urn:staging:schema
```

**Execute a SPARQL UPDATE:**

```bash
ontorag sparql-update --query-file queries/promote_schema.rq
```

**Start the local SPARQL server:**

```bash
ontorag sparql-server \
  --onto data/schema/staging_schema.ttl \
  --inst data/instances/doc_x.instances.ttl \
  --port 8890
```

Endpoints:
- `GET/POST /sparql` -- SPARQL queries (SELECT, ASK, CONSTRUCT, DESCRIBE)
- `GET /health` -- health check with triple count
- `GET /stats` -- SPARQL-based statistics
- `POST /reload` -- reload graph from files

Supports content negotiation: JSON, CSV, TSV, XML, Turtle, N-Triples, JSON-LD.

**Start the knowledge MCP server:**

```bash
# Local TTL backend
ontorag mcp-server \
  --onto data/schema/staging_schema.ttl \
  --inst data/instances/doc_x.instances.ttl

# Remote SPARQL backend
ontorag mcp-server \
  --sparql-endpoint http://localhost:9999/blazegraph/namespace/ontorag/sparql
```

---

## End-to-end workflow

```bash
# 1. Register baseline ontologies
ontorag register-ontology foaf ./ontologies/foaf.ttl --label "FOAF"
ontorag register-ontology prov ./ontologies/prov-o.ttl --label "PROV-O"

# 2. Compose baselines into an initial schema card
ontorag init-schema-card --baselines foaf,prov \
  --out data/schema/schema_card.json

# 3. Ingest a document
ontorag ingest data/raw/report.pdf --out data/dto

# 4. Extract ontology proposals (LLM sees FOAF/PROV terms, reuses them)
ontorag extract-schema \
  --chunks data/dto/chunks/doc_*.jsonl \
  --schema-card data/schema/schema_card.json \
  --out data/proposals/report.schema.json

# 5. Review and merge proposals into the schema card
ontorag build-schema-card \
  --previous data/schema/schema_card.json \
  --proposal data/proposals/report.schema.json \
  --out data/schema/schema_card.json

# 6. Export schema to Turtle
ontorag export-schema-ttl \
  --proposal data/proposals/report.schema.json \
  --out data/schema/staging_schema.ttl

# 7. Extract instances with provenance
ontorag extract-instances \
  --chunks data/dto/chunks/doc_*.jsonl \
  --schema-card data/schema/schema_card.json \
  --out-ttl data/instances/report.instances.ttl

# 8. Inspect the graph locally
ontorag sparql-server \
  --onto data/schema/staging_schema.ttl \
  --inst data/instances/report.instances.ttl

# 9. Expose to LLM agents
ontorag mcp-server \
  --onto data/schema/staging_schema.ttl \
  --inst data/instances/report.instances.ttl
```

---

## Origin tracking

Every class, property, and event in the schema card carries an `origin` field:

| Origin value | Meaning |
|---|---|
| `"foaf"`, `"schema_org"`, ... | Came from a registered baseline ontology |
| `"induced"` | Proposed by the LLM during ontology extraction |
| `""` (empty) | Pre-existing item with unknown origin |

Origin is set when an item first enters the schema card and is preserved across merges. If a baseline defines `Person` and the LLM later proposes `Person` again, the baseline origin is kept.

---

## Project structure

```
ontorag/
  __init__.py
  cli.py                            # Typer CLI (12 commands)
  dto.py                            # DocumentDTO, ChunkDTO, ProvenanceDTO
  extractor_ingest.py               # LlamaIndex document loading + chunking
  storage_jsonl.py                  # JSONL persistence for DTOs
  ontology_extractor_openrouter.py  # LLM schema proposal extraction
  instance_extractor_openrouter.py  # LLM instance extraction
  proposal_aggregator.py            # Merge per-chunk proposals into one
  schema_card.py                    # Deterministic schema card merge (with origin)
  proposal_to_ttl.py                # Schema proposal -> OWL/RDFS Turtle
  instances_to_ttl.py               # Instance proposals -> RDF with provenance
  blazegraph.py                     # Blazegraph REST API integration
  sparql_server.py                  # FastAPI in-memory SPARQL endpoint
  mcp_backend.py                    # SparqlBackend ABC + Local/Remote impls
  mcp_server.py                     # Knowledge graph MCP server
  ontology_catalog.py               # Baseline catalog + OWL/TTL converter
  ontology_mcp.py                   # Ontology catalog MCP server

data/
  ontologies/
    catalog.json                    # Ontology catalog manifest
    *.ttl                           # Registered baseline ontologies
```

---

## What OntoRAG is *not*

- Not a vector-only RAG
- Not a black-box "AI magic" system
- Not a chatbot framework

OntoRAG is a **knowledge engineering system with LLM assistance**.

---

## Status

This project is:

- experimental but functional,
- architecture-first,
- designed for research, enterprise prototyping, and public-sector semantics.

APIs may evolve, concepts will stabilize.

---

## License

Apache 2.0

---

## Philosophy

> If the system cannot explain
> **what it knows**,
> **where it comes from**,
> and **why it changed**,
> it is not a knowledge system.

OntoRAG is built to make that explanation unavoidable.
