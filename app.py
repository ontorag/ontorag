# app.py
"""
Standalone FastAPI app for the OntoRAG ontology catalog.

Delegates all catalog operations to a remote MCP server over SSE.
The MCP endpoint is configurable via ONTORAG_MCP_URL env var
(default: https://mcp.rpg-schema.org/mcp).

Vercel: reads the module-level ``app`` object automatically.
Local:  ``uvicorn app:app --reload``
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware

from ontorag.verbosity import setup_logging, get_logger
from ontorag.mcp_client import OntologyCatalogMCPClient

# ── Configuration ────────────────────────────────────────────────────

MCP_URL = os.getenv("ONTORAG_MCP_URL", "https://mcp.rpg-schema.org/mcp")
VERBOSITY = int(os.getenv("ONTORAG_VERBOSITY", "0"))

setup_logging(VERBOSITY)
_log = get_logger("ontorag.app")

# ── MCP client ───────────────────────────────────────────────────────

_mcp = OntologyCatalogMCPClient(MCP_URL)

# ── FastAPI app ──────────────────────────────────────────────────────

app = FastAPI(
    title="OntoRAG Ontology Catalog",
    version="0.1.0",
    description="Browse, search, and compose baseline ontologies for OntoRAG via MCP.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_log.info("Ontology catalog app starting (mcp=%s)", MCP_URL)


# ── Health ───────────────────────────────────────────────────────────

@app.get("/")
async def root():
    try:
        data = await _mcp.list_ontologies()
        count = data.get("count", 0)
    except Exception:
        count = -1
    return {
        "service": "ontorag-ontology-catalog",
        "status": "ok",
        "mcp_url": MCP_URL,
        "ontologies_count": count,
    }


@app.get("/health")
def health():
    return {"ok": True, "mcp_url": MCP_URL}


# ── List ontologies ──────────────────────────────────────────────────

@app.get("/ontologies")
async def list_ontologies():
    """List all registered baseline ontologies."""
    _log.debug("GET /ontologies")
    try:
        return await _mcp.list_ontologies()
    except Exception as exc:
        _log.info("MCP error: %s", exc)
        raise HTTPException(status_code=502, detail=f"MCP error: {exc}")


# ── Inspect ontology ────────────────────────────────────────────────

@app.get("/ontologies/{slug}")
async def inspect_ontology(slug: str):
    """Inspect a baseline ontology: classes, properties, schema card."""
    _log.debug("GET /ontologies/%s", slug)
    try:
        data = await _mcp.inspect_ontology(slug)
    except Exception as exc:
        _log.info("MCP error: %s", exc)
        raise HTTPException(status_code=502, detail=f"MCP error: {exc}")

    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    return data


# ── Search ───────────────────────────────────────────────────────────

@app.get("/search/classes")
async def search_classes(q: str = Query(..., min_length=1, description="Search term")):
    """Search for classes across all registered ontologies."""
    _log.debug("GET /search/classes?q=%s", q)
    try:
        return await _mcp.search_classes(q)
    except Exception as exc:
        _log.info("MCP error: %s", exc)
        raise HTTPException(status_code=502, detail=f"MCP error: {exc}")


@app.get("/search/properties")
async def search_properties(q: str = Query(..., min_length=1, description="Search term")):
    """Search for properties (datatype + object) across all ontologies."""
    _log.debug("GET /search/properties?q=%s", q)
    try:
        return await _mcp.search_properties(q)
    except Exception as exc:
        _log.info("MCP error: %s", exc)
        raise HTTPException(status_code=502, detail=f"MCP error: {exc}")


# ── Compose ──────────────────────────────────────────────────────────

@app.post("/compose")
async def compose(
    body: Dict[str, Any] = Body(
        ...,
        examples=[{"slugs": ["foaf", "schema_org"], "target_namespace": ""}],
    ),
):
    """Compose multiple baseline ontologies into a single schema card."""
    slugs = body.get("slugs", [])
    if not slugs:
        raise HTTPException(status_code=422, detail="Provide at least one slug.")
    ns = body.get("target_namespace", "")
    _log.debug("POST /compose slugs=%s", slugs)
    try:
        return await _mcp.compose(slugs, target_namespace=ns)
    except Exception as exc:
        _log.info("MCP error: %s", exc)
        raise HTTPException(status_code=502, detail=f"MCP error: {exc}")


# ── Register / add ontology ─────────────────────────────────────────

@app.post("/ontologies")
async def add_ontology(
    body: Dict[str, Any] = Body(
        ...,
        examples=[{
            "slug": "foaf",
            "ttl_content": "@prefix ...",
            "label": "FOAF",
            "description": "Friend of a Friend",
            "tags": ["social"],
        }],
    ),
):
    """Register a new baseline ontology by providing its TTL content."""
    slug = body.get("slug", "").strip()
    ttl_content = body.get("ttl_content", "").strip()
    if not slug or not ttl_content:
        raise HTTPException(status_code=422, detail="slug and ttl_content are required.")

    _log.debug("POST /ontologies slug=%s", slug)
    try:
        return await _mcp.add_ontology(
            slug=slug,
            ttl_content=ttl_content,
            label=body.get("label", ""),
            description=body.get("description", ""),
            tags=body.get("tags", []),
        )
    except Exception as exc:
        _log.info("MCP error: %s", exc)
        raise HTTPException(status_code=502, detail=f"MCP error: {exc}")
