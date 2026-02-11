# OntoRAG System Architecture Review

## Overall Assessment

The architecture is **coherent and well-designed**. The pipeline follows a clear, principled flow:

```
Documents → DTOs → LLM Proposals → Schema Card → Instance Extraction → RDF/TTL → SPARQL/MCP
```

The core philosophy — "LLMs propose, Code decides, Humans govern" — is consistently applied throughout. Each stage has a clear responsibility, data flows forward through well-defined interfaces, and the separation between LLM-generated proposals and deterministic merging is sound.

---

## What Works Well

1. **DTO-first ingestion** (`dto.py`, `extractor_ingest.py`, `storage_jsonl.py`) — Clean Pydantic models with stable IDs (SHA1-based), provenance tracking, and JSONL persistence. The DTOs serve as a replayable checkpoint.

2. **Two-phase schema evolution** — LLM proposals (`ontology_extractor_openrouter.py`) are aggregated (`proposal_aggregator.py`) then deterministically merged into the schema card (`schema_card.py`). No hidden ML decisions in the merge step.

3. **Evidence/provenance threading** — Every proposed class, property, and instance carries evidence quotes back to source chunks. The instance RDF embeds PROV-style mention nodes linking facts to their textual origin.

4. **Schema card as governance artifact** — Versioned, human-reviewable JSON that guides all downstream extraction. This prevents schema drift and makes the ontology auditable.

5. **Multi-backend design** — Local rdflib for inspection, Blazegraph for production. SPARQL as the universal query interface.

6. **MCP integration** — Exposes the knowledge graph as structured tools for LLM agents, cleanly separating reasoning from data.

---

## Critical Issues

### 1. Missing `mcp_backend` module

**Files affected:** `cli.py:228`, `mcp_server.py:7`

Both files import from `ontorag.mcp_backend`:
- `cli.py`: `from ontorag.mcp_backend import LocalRdfBackend, RemoteSparqlBackend`
- `mcp_server.py`: `from ontorag.mcp_backend import SparqlBackend`

**No `mcp_backend.py` file exists in the codebase.** This means:
- The `ontorag mcp-server` CLI command will crash with `ModuleNotFoundError`
- The `mcp_server.py` module cannot be imported at all

**Expected contents:** A `SparqlBackend` abstract base class with `select()` and `construct()` methods, and two implementations: `LocalRdfBackend` (wrapping rdflib in-memory graph) and `RemoteSparqlBackend` (wrapping a remote SPARQL endpoint via HTTP).

### 2. SPARQL server filename is both misspelled and uses an invalid Python module name

**File:** `ontorag/saprql-server.py`
**Import in CLI:** `from ontorag.sparql_server import create_app` (`cli.py:199`)

Two problems:
- The filename is misspelled: `saprql-server.py` instead of `sparql_server.py`
- Hyphens are invalid in Python module names — Python cannot import `saprql-server` as a module

The `ontorag sparql-server` CLI command will fail with `ModuleNotFoundError`.

**Fix:** Rename `saprql-server.py` → `sparql_server.py`

### 3. `ontology_extractor.py` has a broken import and is dead code

**File:** `ontorag/ontology_extractor.py:5`

```python
from ontology_proposal import ChunkOntologyProposal
```

This imports from a root-level file (`ontology_proposal.py`) using a bare import. As a package module, this will fail unless the root directory is manually added to `sys.path`. Additionally, nothing in the codebase imports `ontology_extractor.py` — the actual extractor used is `ontology_extractor_openrouter.py`.

---

## Moderate Issues

### 4. `blazegraph_upload_ttl` injects raw TTL into SPARQL UPDATE

**File:** `blazegraph.py:20-27`

Raw Turtle content is string-interpolated directly into a SPARQL UPDATE query:
```python
update = f"""
INSERT DATA {{
  GRAPH <{graph_iri}> {{
{ttl}
  }}
}}
"""
```

Turtle syntax can contain curly braces, angle brackets, and other characters that conflict with SPARQL syntax. This will break for non-trivial TTL files. A more robust approach would use Blazegraph's REST API for bulk graph loading (e.g., POST to the endpoint with `Content-Type: application/x-turtle`).

### 5. No rate limiting in instance extraction

**File:** `instance_extractor_openrouter.py:118-139`

The schema extractor (`ontology_extractor_openrouter.py:98`) includes `time.sleep(10)` between chunk calls, but the instance extractor has no inter-chunk delay. For documents with many chunks, this will likely hit OpenRouter rate limits.

### 6. SPARQL query type detection is fragile

**File:** `saprql-server.py:12-22`

`_detect_query_kind()` checks if the query starts with a SPARQL keyword, then checks for keywords after newlines. Single-line queries with PREFIX blocks (e.g., `PREFIX foo: <...> SELECT ...`) will not be detected and will fall through to the "unknown" fallback. The fallback does work (it tries the query and checks the result type), so this is not fatal, but it's fragile.

### 7. SPARQL injection in MCP server tools

**File:** `mcp_server.py:26-51`

Tools like `describe()`, `list_by_class()`, `outgoing()`, and `incoming()` inject IRIs directly into SPARQL query strings via f-strings:
```python
q = f"DESCRIBE <{iri}>"
```

If an IRI contains `>` or other special characters, the query will break or behave unexpectedly. Since these are MCP tools exposed to LLM agents, malformed input is plausible.

### 8. `pyproject.toml` missing runtime dependencies

**File:** `pyproject.toml`

Only 6 dependencies are listed (typer, requests, pydantic, rdflib, llama-index, python-dotenv), but the codebase also requires at runtime:
- `fastapi` + `uvicorn` (SPARQL server)
- `fastmcp` (MCP server)
- `openai` (used by `openrouter_client.py`)

Users installing via `pip install .` will get incomplete dependencies.

---

## Minor Issues

### 9. `datetime.utcnow()` is deprecated

**Files:** `dto.py:34,43`, `schema_card.py:9`

Since Python 3.12, `datetime.utcnow()` is deprecated in favor of `datetime.now(datetime.timezone.utc)`.

### 10. Legacy root-level files with broken imports

Three files at the project root are leftover from an earlier iteration:
- `cli_extract.py` — imports `from extractor import ...` (should be `extractor_ingest`)
- `openrouter_client.py` — standalone OpenAI client factory, unused by package
- `ontology_proposal.py` — Pydantic models superseded by the dict-based approach in the package

None of these are importable from the package or referenced by it.

### 11. `openrouter_client.py` returns string, not dict

**File:** `openrouter_client.py:28`

The function `chat_json` returns `resp.choices[0].message.content` which is a raw string, not parsed JSON, despite the function name suggesting otherwise. The comment acknowledges this (`# JSON string (poi json.loads)`), but the API is misleading.

### 12. Schema card class name validation is case-sensitive for warnings but case-insensitive for dedup

**File:** `schema_card.py:215-223`

Classes are deduplicated by lowercase key (`_key_class`), but the warning check `p["domain"] not in class_names` uses the original-case `name` field. If a property references a class with different casing (e.g., "person" vs "Person"), it will exist in the deduplicated map but trigger a spurious warning.

---

## Data Flow Verification

| Step | Input | Output | Verified |
|------|-------|--------|----------|
| `ingest` | File path | DocumentDTO + ChunkDTOs (JSONL) | OK |
| `extract-schema` | Chunks JSONL + schema card | Aggregated proposal JSON | OK |
| `build-schema-card` | Previous card + proposal | New schema card JSON | OK |
| `export-schema-ttl` | Proposal JSON | OWL/RDFS Turtle | OK |
| `extract-instances` | Chunks JSONL + schema card | Instance RDF TTL with provenance | OK |
| `load-ttl` | TTL file + graph IRI | Blazegraph upload | OK (with caveat #4) |
| `sparql-server` | Ontology TTL + Instances TTL | FastAPI SPARQL endpoint | BROKEN (issue #2) |
| `mcp-server` | TTL or SPARQL endpoint | MCP tools | BROKEN (issue #1) |

---

## Recommendations (Priority Order)

1. **Create `ontorag/mcp_backend.py`** with `SparqlBackend`, `LocalRdfBackend`, and `RemoteSparqlBackend`
2. **Rename `saprql-server.py` → `sparql_server.py`**
3. **Fix `blazegraph_upload_ttl`** to use Blazegraph REST API instead of SPARQL INSERT DATA
4. **Add missing dependencies** to `pyproject.toml` (fastapi, uvicorn, fastmcp)
5. **Add inter-chunk delay** in `instance_extractor_openrouter.py`
6. **Sanitize IRI inputs** in MCP server SPARQL templates
7. **Remove or relocate legacy files** (`cli_extract.py`, `openrouter_client.py`, `ontology_proposal.py`, `ontology_extractor.py`)
8. **Add a test suite** — the project has zero tests

---

## Conclusion

The OntoRAG system architecture is **fundamentally sound**. The pipeline design, governance model, and separation of concerns are well thought out. The two blocking issues (missing `mcp_backend` module and misspelled SPARQL server filename) prevent two CLI commands from working, but the core extraction pipeline (`ingest` → `extract-schema` → `build-schema-card` → `export-schema-ttl` → `extract-instances`) is complete and internally consistent.
