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

