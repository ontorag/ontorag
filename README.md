# OntoRAG

**OntoRAG** is an ontology-first alternative to traditional Retrieval-Augmented Generation (RAG).

Instead of retrieving text fragments and hoping the LLM reasons correctly, OntoRAG:
- extracts **explicit structure** from documents,
- builds a governed **knowledge graph** (RDF),
- and uses LLMs only where they add value: proposal, extraction, interpretation.

The result is a system that is:
- inspectable,
- auditable,
- evolvable,
- and usable beyond chat.

---

## Why OntoRAG exists

Traditional RAG systems suffer from structural weaknesses:

- No explicit domain model  
- No traceability from answers to sources  
- No governance or evolution of knowledge  
- Hidden schema inside prompts and embeddings  

OntoRAG flips the model:

> **Documents → DTOs → Ontology → Instances → SPARQL → MCP tools → LLM reasoning**

LLMs *propose*.  
Code *decides*.  
Humans *govern*.

---

## Core concepts

### 1. DTO-first ingestion
Documents are parsed using best-in-class loaders (via LlamaIndex) into stable **DocumentDTO / ChunkDTO** objects.

DTOs are:
- format-agnostic (PDF, Markdown, CSV, RDF…),
- persistent,
- replayable,
- provenance-aware.

They are the semantic checkpoint of the pipeline.

---

### 2. Ontology induction (proposal, not truth)
LLMs analyze DTO chunks and propose:
- candidate classes,
- datatype properties,
- object properties,
- events,
- merge/alias suggestions.

These are **proposals**, not production schema.

---

### 3. Schema Card
The **Schema Card** is a compact, deterministic JSON description of the current ontology:

- classes
- properties
- relations
- events
- aliases
- warnings

It is:
- versioned,
- human-reviewable,
- used to guide future extraction and MCP tool generation.

---

### 4. Instance extraction with provenance
Given a stable schema card, OntoRAG extracts **instances** from documents:

- RDF instances
- linked to their source chunks
- with quotes and location metadata
- using PROV-style relations

No hallucinated facts, no orphan triples.

---

### 5. Knowledge graph backends
OntoRAG supports two modes:

- **Local inspection**: in-memory RDF (rdflib)
- **Production-grade**: external SPARQL engines  
  (Blazegraph, QLever, others)

Both are exposed via standard SPARQL.

---

### 6. MCP integration
OntoRAG can spawn an **MCP server** that exposes:
- SPARQL-backed tools,
- graph navigation helpers,
- describe/list/search operations.

This allows:
- LLM agents to query structured truth,
- external systems to automate workflows,
- clean separation between reasoning and data.

---

## Architecture overview

```

Documents
│
▼
DTOs (Document / Chunk)
│
├─ Ontology Extraction (LLM → proposals)
│
├─ Schema Card (deterministic merge)
│
├─ Instance Extraction (LLM → RDF)
│
▼
Knowledge Graph (TTL / SPARQL)
│
├─ SPARQL endpoint
└─ MCP Server

````

---

## CLI usage

OntoRAG ships with a **Typer-based CLI**.

### Ingest documents
```bash
ontorag ingest data/raw/manual.pdf --out data/dto
````

### Extract ontology proposals

```bash
ontorag extract-schema \
  --chunks data/dto/chunks/doc_x.jsonl \
  --schema-card data/schema/schema_card.json \
  --out data/proposals/doc_x.schema.json
```

### Build schema card (deterministic)

```bash
ontorag build-schema-card \
  --previous data/schema/schema_card.json \
  --proposal data/proposals/doc_x.schema.json \
  --out data/schema/schema_card.next.json
```

### Extract instances

```bash
ontorag extract-instances \
  --chunks data/dto/chunks/doc_x.jsonl \
  --schema-card data/schema/schema_card.json \
  --out-ttl data/instances/doc_x.instances.ttl
```

---

## Inspect the graph (local SPARQL server)

```bash
ontorag sparql-server \
  --onto data/schema/staging_schema.ttl \
  --inst data/instances/doc_x.instances.ttl \
  --port 8890
```

Endpoints:

* `GET /sparql`
* `POST /sparql`
* `GET /health`
* `GET /stats`

---

## MCP server

### Local TTL backend

```bash
ontorag mcp-server \
  --onto data/schema/staging_schema.ttl \
  --inst data/instances/doc_x.instances.ttl
```

### Remote SPARQL backend

```bash
ontorag mcp-server \
  --sparql-endpoint http://localhost:9999/blazegraph/namespace/ontorag/sparql
```

The MCP server exposes SPARQL-powered tools for agents and automations.

---

## Configuration

OntoRAG uses a `.env` file (loaded automatically):

```env
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

BLAZEGRAPH_ENDPOINT=http://localhost:9999/blazegraph/namespace/ontorag/sparql
```

---

## What OntoRAG is *not*

* Not a vector-only RAG
* Not a black-box “AI magic” system
* Not a chatbot framework

OntoRAG is a **knowledge engineering system with LLM assistance**.

---

## Status

This project is:

* experimental but functional,
* architecture-first,
* designed for research, enterprise prototyping, and public-sector semantics.

APIs may evolve, concepts will stabilize.

---

## License

Apache 2.0 (or decide otherwise)

---

## Philosophy

> If the system cannot explain
> **what it knows**,
> **where it comes from**,
> and **why it changed**,
> it is not a knowledge system.

OntoRAG is built to make that explanation unavoidable.
