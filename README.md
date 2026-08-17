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

Documents are **content-hashed** (SHA-256) before any processing occurs. The document ID is derived from the hash, making ingestion **content-addressable**: the same file ingested from different paths or at different times produces the same `document_id`. If a document has already been ingested, the pipeline skips re-chunking automatically (`--force` to override).

Documents are parsed by a **pluggable ingest engine** (`--engine`). The default engine, **`builtin`**, has no external dependencies and no API keys: Markdown/text via a local recursive splitter, EPUB/HTML via `ebooklib` + `html2text`, and PDF via **PyMuPDF** (`pip install 'ontorag[pdf]'`). Additional engines are available as optional extras — `pageindex` (hosted hierarchical PDF), `llamaindex`, `docling` (IBM layout-aware), and `unstructured` (typed elements). Run `ontorag doctor` to see which engines are installed. The result is stable **DocumentDTO / ChunkDTO** objects, independent of the engine used.

DTOs are:
- content-addressable (same content = same document ID, no re-processing),
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

OntoRAG is on PyPI (Python ≥ 3.12):

```bash
pip install ontorag                 # core — ingests Markdown/text/HTML/EPUB out of the box
pip install 'ontorag[pdf]'          # + PDF ingest via PyMuPDF (builtin engine)
```

Optional ingest engines are extras — install only what you need:

```bash
pip install 'ontorag[pageindex]'    # hosted hierarchical PDF (needs PAGEINDEX_API_KEY)
pip install 'ontorag[llamaindex]'   # LlamaIndex fixed-chunk ingest
pip install 'ontorag[docling]'      # IBM Docling, layout-aware PDF/DOCX/PPTX
pip install 'ontorag[unstructured]' # Unstructured typed elements
```

Core dependencies (always installed): `typer`, `requests`, `pydantic`, `rdflib`, `python-dotenv`, `fastapi`, `uvicorn`, `fastmcp`, `mcp`, `EbookLib`, `html2text`, `httpx`, `PyJWT`, `python-multipart`. Parsers (`pymupdf`, `pageindex`, `llama-index`, `docling`, `unstructured`) are **optional extras**.

After installing, check your environment and available engines:

```bash
ontorag doctor
```

For local development (editable install):

```bash
pip install -e '.[pdf,dev]'
```

---

## Configuration

Copy the example environment file and fill in your API key:

```bash
cp .example.env .env
```

```env
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=~deepseek/deepseek-v4-flash-latest
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_APP_NAME=OntoRAG
OPENROUTER_SITE_URL=https://ontorag.github.io

# Optional: only needed for load-ttl / sparql-update commands
BLAZEGRAPH_ENDPOINT=http://localhost:9999/blazegraph/namespace/ontorag/sparql
```

**Model choice matters.** Every LLM step (`extract-schema`, `align-schema`,
`extract-instances`) sends the full schema card in each prompt, so a fast,
capable model is worth it. `~deepseek/deepseek-v4-flash-latest` (the tilde `~`
is part of the OpenRouter "latest" alias; it resolves to the newest
`deepseek/deepseek-v4-flash`) is a good default — validated end-to-end below.
Avoid `*:free` slugs for real runs: they are frequently retired and the shared
free router is slow enough to stall multi-chunk instance extraction.

**Local / self-hosted models.** Point `OPENROUTER_BASE_URL` at any
OpenAI-compatible endpoint to run fully offline — e.g. a local Ollama:

```env
OPENROUTER_BASE_URL=http://localhost:11434/v1
OPENROUTER_MODEL=qwen2.5:14b
OPENROUTER_API_KEY=ollama    # any non-empty value
```

**Overriding per-invocation (CLI flags).** Every OpenRouter setting is also a
global flag, placed *before* the subcommand. Flags win over environment
variables, which win over built-in defaults:

```bash
ontorag --model '~deepseek/deepseek-v4-flash-latest' --api-key sk-... \
        extract-schema --chunks c.jsonl --schema-card card.json --out prop.json

# run against a local ollama without touching the environment
ontorag --base-url http://localhost:11434/v1 --api-key ollama --model qwen2.5:14b \
        extract-instances --chunks c.jsonl --schema-card card.json --out-ttl inst.ttl
```

| Flag | Overrides / env | Applies to |
|---|---|---|
| `--model` / `-m` | `OPENROUTER_MODEL` | extract-schema, align-schema, extract-instances |
| `--api-key` | `OPENROUTER_API_KEY` | ” |
| `--base-url` | `OPENROUTER_BASE_URL` | ” |
| `--app-name` | `OPENROUTER_APP_NAME` | ” |
| `--site-url` | `OPENROUTER_SITE_URL` | ” |
| `--concurrency` / `-j` | `ONTORAG_CONCURRENCY` (default 4) | extract-schema, extract-instances |
| `--slim-card` | `ONTORAG_SLIM_CARD` (default off) | extract-schema, extract-instances |

**Speed:** the per-chunk LLM call is latency-bound, so extraction runs chunks
**concurrently** (`-j`, default 4). Raise it for long documents (`-j 8`), or set
`-j 1` for strictly sequential. `--slim-card` prunes the schema card to
chunk-relevant terms — smaller, cheaper prompts, but it can lower
instance-extraction recall on large baselines, so it is **off by default**.

`ontorag doctor` prints the effective model, base URL, and whether a key is set.

---

## CLI reference

All commands are available via `ontorag <command> --help`. LLM settings are
global flags placed before the subcommand (see *Configuration* above).

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
ontorag ingest data/raw/manual.pdf --out data/dto          # builtin engine (default)
ontorag ingest data/raw/handbook.epub --out data/dto

# Pick a different engine explicitly (or set ONTORAG_INGEST_ENGINE):
ontorag ingest data/raw/manual.pdf --engine docling --out data/dto
ontorag ingest data/raw/manual.pdf --engine pageindex --out data/dto   # needs PAGEINDEX_API_KEY

# Re-ingesting the same file is a no-op (content-hashed):
ontorag ingest data/raw/manual.pdf --out data/dto
# → SKIP ingest: already ingested (document_id=doc_..., hash=...)

# Force re-ingest:
ontorag ingest data/raw/manual.pdf --out data/dto --force
```

The file is **content-hashed** (SHA-256) before chunking. If the same content was already ingested, the command skips processing and reports the existing document ID. Use `--force` to re-ingest anyway.

The engine is selected with `--engine {builtin|pageindex|llamaindex|docling|unstructured}` (default `builtin`, no keys/deps). Whatever the engine, the output is the same stable DocumentDTO + ChunkDTOs (JSON + JSONL). Run `ontorag doctor` to see which engines are installed:

```bash
ontorag doctor
# → OntoRAG environment
#   LLM: OpenRouter (model=~deepseek/deepseek-v4-flash-latest)
#   ingest engines: builtin ✓  pageindex ✓  llamaindex (pip install 'ontorag[llamaindex]')  ...
```

**Extract ontology proposals:**

```bash
ontorag extract-schema \
  --chunks data/dto/chunks/doc_x.jsonl \
  --schema-card data/schema/schema_card.json \
  --out data/proposals/doc_x.schema.json
```

Sends each chunk + the current schema card to the LLM. The LLM proposes new classes, properties, events, and merge suggestions. Per-chunk proposals are aggregated into a single document-level proposal.

**Align the proposal to a baseline (optional but recommended):**

```bash
ontorag align-schema \
  --proposal data/proposals/doc_x.schema.json \
  --baseline data/schema/schema_card.json \
  --out data/proposals/doc_x.alignment.json
```

For each induced class/property, the LLM decides whether it should **reuse** a baseline term, **extend** one (subclass/subproperty), or stand as **new** — with a rationale for each decision. This keeps the graph anchored to standard vocabularies instead of reinventing them. Alignment supports **partial-save and auto-resume**: if interrupted, re-running resumes from the last completed category. The aligned JSON is a drop-in replacement for the raw proposal in the next two steps.

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

### Publish to the Hub

**Push a locally-built dataset to GitHub** so the [OntoRAG Hub](https://github.com/ontorag/hub)
can explore or fork it. It synthesizes a Hub-compatible `manifest.json` (the
`ontorag` spec version + an `ontology.graph` pointer + entity counts, inferred
straight from the graph) when the directory doesn't already have one, then
commits the dataset in one commit via the GitHub API. Auth is a GitHub token
with `repo` scope, via `--token` or `GITHUB_TOKEN` / `GH_TOKEN`.

```bash
# publish the whole dataset (ontology + graph + the original corpus)
ontorag hub push ./my-dataset --repo myorg/my-dataset

# publish only the derived ontology + graph, not the source documents
ontorag hub push ./my-dataset --repo myorg/my-dataset --no-include-sources --public
```

Key options: `--include-sources/--no-include-sources` (upload the raw corpus
under `content/sources/` or only the derived ontology + graph), `--private/--public`
(visibility on creation), `--graph` (path to the world/instance TTL, default
`ontology/world.ttl`), `--base-iri` / `--name` / `--license` (override the
generated manifest), `--regenerate-manifest` (rewrite an existing manifest).
Re-running updates the repo; intermediate DTOs and `.env` are never uploaded.

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

# 5. Align the proposal to the baseline (reuse / extend / new)
ontorag align-schema \
  --proposal data/proposals/report.schema.json \
  --baseline data/schema/schema_card.json \
  --out data/proposals/report.alignment.json

# 6. Merge the aligned proposal into the schema card
ontorag build-schema-card \
  --previous data/schema/schema_card.json \
  --proposal data/proposals/report.alignment.json \
  --out data/schema/schema_card.json

# 7. Export schema to Turtle
ontorag export-schema-ttl \
  --proposal data/proposals/report.alignment.json \
  --out data/schema/staging_schema.ttl

# 8. Extract instances with provenance
ontorag extract-instances \
  --chunks data/dto/chunks/doc_*.jsonl \
  --schema-card data/schema/schema_card.json \
  --out-ttl data/instances/report.instances.ttl

# 9. Inspect the graph locally
ontorag sparql-server \
  --onto data/schema/staging_schema.ttl \
  --inst data/instances/report.instances.ttl

# 10. Expose to LLM agents
ontorag mcp-server \
  --onto data/schema/staging_schema.ttl \
  --inst data/instances/report.instances.ttl

# 11. (optional) Publish to the Hub — explore/fork it from the web
ontorag hub push . --repo myorg/report-dataset --no-include-sources
```

---

## Validated end-to-end run

The full pipeline has been validated against the public
[**rpg-schema**](https://github.com/rpg-schema/rpg-schema.github.io) baseline
ontology (68 classes / 47 datatype / 93 object properties) using
`~deepseek/deepseek-v4-flash-latest`, on two independent, unrelated RPG
rulebooks — proving the process is corpus-agnostic (same commands, same
baseline, different documents):

| Stage | Daggerheart SRD (0.9 MB PDF) | D&D 5.2.1 SRD (6 MB PDF) |
|---|---|---|
| `ingest` (builtin / PyMuPDF) | 181 chunks | 503 chunks |
| `extract-schema` (4 chunks) | 17 classes, 5 dt, 9 obj | 66 classes, 0 dt, 5 obj |
| `align-schema` → rpg baseline | reuse 0 · extend 11 · **new 20** | reuse 0 · extend 41 · **new 30** |
| `export-schema-ttl` | 70 triples | 122 triples |
| `extract-instances` (4 chunks) | 63 instances, **605 triples** | validated on Daggerheart (see note) |
| instance types found | Character, GameMaster, DualityDice, DeathMove, RuleSet, CampaignFrame, … | 41 induced classes `extend` rpg-schema classes |

Both runs use the identical command sequence and the identical `rpg-schema`
baseline — only the input file changes. The D&D run drove the alignment harder
(66 induced classes vs 17): 41 were aligned as **`extend`** (domain
specializations — subclasses of rpg-schema classes) and 30 as **`new`**, each
with a recorded rationale.

> **Note on `extract-instances` speed.** The per-chunk LLM call is
> latency-bound (a minute or more each on some hosted models), so the extractors
> process chunks **concurrently** (`-j`, default 4) — a long document sees a
> roughly N× wall-clock speedup. Raise `-j` for large corpora. Prompt *size* is
> a much smaller factor; `--slim-card` trims it further (cheaper tokens) at some
> recall cost, so it is off by default.

Reproduce it (any RPG PDF works — swap the file):

```bash
pip install 'ontorag[pdf]'
export OPENROUTER_API_KEY=sk-...
export OPENROUTER_MODEL='~deepseek/deepseek-v4-flash-latest'

# rpg-schema as the baseline ontology
curl -sL https://raw.githubusercontent.com/rpg-schema/rpg-schema.github.io/refs/heads/main/src/data/rpg-schema.ttl -o rpg-schema.ttl
ontorag register-ontology rpg ./rpg-schema.ttl --catalog data/ont --label "RPG Schema"
ontorag init-schema-card --baselines rpg --catalog data/ont \
  --namespace http://ontorag.dev/dh/ --out data/schema/card.json

# ingest → induce → align → merge → export → instances
ontorag ingest your-rulebook.pdf --out data/dto
CH=$(ls data/dto/chunks/*.jsonl | head -1)
ontorag extract-schema  --chunks "$CH" --schema-card data/schema/card.json --out data/proposal.json
ontorag align-schema    --proposal data/proposal.json --baseline data/schema/card.json --out data/alignment.json
ontorag build-schema-card --previous data/schema/card.json --proposal data/alignment.json --out data/schema/card2.json
ontorag export-schema-ttl --proposal data/alignment.json --namespace http://ontorag.dev/dh/ --out data/schema.ttl
ontorag extract-instances --chunks "$CH" --schema-card data/schema/card2.json --out-ttl data/instances.ttl
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
  cli.py                            # Typer CLI (15 commands, incl. doctor and hub push)
  llm_config.py                     # OpenRouter settings resolver (CLI flag > env > default)
  hub_push.py                       # publish a dataset to GitHub for the Hub (manifest synth + Git Data API)
  parallel.py                       # bounded-concurrency chunk processing (--concurrency)
  card_slim.py                      # opt-in per-chunk schema-card pruning (--slim-card)
  dto.py                            # DocumentDTO, ChunkDTO, ProvenanceDTO + content hashing
  extractor_ingest.py               # pluggable ingest engines (builtin default; pageindex/llamaindex/docling/unstructured)
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
  mcp_client.py                     # Async SSE client for remote MCP
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
