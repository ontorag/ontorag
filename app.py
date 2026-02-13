# app.py
"""
Standalone FastAPI app for the OntoRAG ontology catalog.

Exposes the catalog as REST endpoints for HTTP clients and mounts
the MCP server at /mcp for MCP-compatible agents.

Vercel: reads the module-level ``app`` object automatically.
Local:  ``uvicorn app:app --reload``
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware

from ontorag.verbosity import setup_logging, get_logger
from ontorag.ontology_catalog import (
    load_catalog,
    register_ontology,
    ttl_to_schema_card,
    compose_baselines,
)

# ── Configuration ────────────────────────────────────────────────────

CATALOG_DIR = os.getenv("ONTORAG_CATALOG_DIR", "./data/ontologies")
VERBOSITY = int(os.getenv("ONTORAG_VERBOSITY", "0"))

setup_logging(VERBOSITY)
_log = get_logger("ontorag.app")

# ── FastAPI app ──────────────────────────────────────────────────────

app = FastAPI(
    title="OntoRAG Ontology Catalog",
    version="0.1.0",
    description="Browse, search, and compose baseline ontologies for OntoRAG.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_log.info("Ontology catalog app starting (catalog=%s)", CATALOG_DIR)

# Ensure catalog dir exists
Path(CATALOG_DIR).mkdir(parents=True, exist_ok=True)

# ── Health ───────────────────────────────────────────────────────────

@app.get("/")
def root():
    catalog = load_catalog(CATALOG_DIR)
    count = len(catalog.get("ontologies", []))
    return {
        "service": "ontorag-ontology-catalog",
        "status": "ok",
        "catalog_dir": CATALOG_DIR,
        "ontologies_count": count,
    }


@app.get("/health")
def health():
    return {"ok": True}


# ── List ontologies ──────────────────────────────────────────────────

@app.get("/ontologies")
def list_ontologies():
    """List all registered baseline ontologies."""
    _log.debug("GET /ontologies")
    catalog = load_catalog(CATALOG_DIR)
    entries = catalog.get("ontologies", [])
    return {
        "count": len(entries),
        "ontologies": [
            {
                "slug": e["slug"],
                "label": e.get("label", e["slug"]),
                "namespace": e.get("namespace", ""),
                "description": e.get("description", ""),
                "tags": e.get("tags", []),
            }
            for e in entries
        ],
    }


# ── Inspect ontology ────────────────────────────────────────────────

@app.get("/ontologies/{slug}")
def inspect_ontology(slug: str):
    """Inspect a baseline ontology: classes, properties, schema card."""
    _log.debug("GET /ontologies/%s", slug)
    catalog = load_catalog(CATALOG_DIR)
    entry = next(
        (e for e in catalog.get("ontologies", []) if e["slug"] == slug), None
    )
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Ontology '{slug}' not found.")

    ttl_file = str(Path(CATALOG_DIR) / entry["path"])
    card = ttl_to_schema_card(ttl_file, slug, namespace=entry.get("namespace"))
    return {
        "slug": slug,
        "label": entry.get("label", slug),
        "namespace": entry.get("namespace", ""),
        "classes_count": len(card.get("classes", [])),
        "datatype_properties_count": len(card.get("datatype_properties", [])),
        "object_properties_count": len(card.get("object_properties", [])),
        "schema_card": card,
    }


# ── Search ───────────────────────────────────────────────────────────

@app.get("/search/classes")
def search_classes(q: str = Query(..., min_length=1, description="Search term")):
    """Search for classes across all registered ontologies."""
    _log.debug("GET /search/classes?q=%s", q)
    q_lower = q.strip().lower()
    catalog = load_catalog(CATALOG_DIR)
    results: List[Dict[str, Any]] = []

    for entry in catalog.get("ontologies", []):
        ttl_file = str(Path(CATALOG_DIR) / entry["path"])
        card = ttl_to_schema_card(ttl_file, entry["slug"], namespace=entry.get("namespace"))
        for cls in card.get("classes", []):
            name = cls.get("name", "")
            desc = cls.get("description", "")
            if q_lower in name.lower() or q_lower in desc.lower():
                results.append({
                    "ontology": entry["slug"],
                    "class": name,
                    "description": desc,
                })

    return {"query": q, "count": len(results), "results": results}


@app.get("/search/properties")
def search_properties(q: str = Query(..., min_length=1, description="Search term")):
    """Search for properties (datatype + object) across all ontologies."""
    _log.debug("GET /search/properties?q=%s", q)
    q_lower = q.strip().lower()
    catalog = load_catalog(CATALOG_DIR)
    results: List[Dict[str, Any]] = []

    for entry in catalog.get("ontologies", []):
        ttl_file = str(Path(CATALOG_DIR) / entry["path"])
        card = ttl_to_schema_card(ttl_file, entry["slug"], namespace=entry.get("namespace"))
        for p in card.get("datatype_properties", []) + card.get("object_properties", []):
            searchable = " ".join([
                p.get("name", ""),
                p.get("domain", ""),
                p.get("range", ""),
                p.get("description", ""),
            ]).lower()
            if q_lower in searchable:
                results.append({
                    "ontology": entry["slug"],
                    "property": p["name"],
                    "domain": p.get("domain", ""),
                    "range": p.get("range", ""),
                    "description": p.get("description", ""),
                })

    return {"query": q, "count": len(results), "results": results}


# ── Compose ──────────────────────────────────────────────────────────

@app.post("/compose")
def compose(
    body: Dict[str, Any] = Body(
        ...,
        examples=[{"slugs": ["foaf", "schema_org"], "target_namespace": ""}],
    ),
):
    """Compose multiple baseline ontologies into a single schema card."""
    slugs = body.get("slugs", [])
    if not slugs:
        raise HTTPException(status_code=422, detail="Provide at least one slug.")
    ns = body.get("target_namespace") or None
    _log.debug("POST /compose slugs=%s", slugs)
    card = compose_baselines(CATALOG_DIR, slugs, target_namespace=ns)
    return {
        "baselines_used": slugs,
        "classes_count": len(card.get("classes", [])),
        "datatype_properties_count": len(card.get("datatype_properties", [])),
        "object_properties_count": len(card.get("object_properties", [])),
        "schema_card": card,
    }


# ── Register / add ontology ─────────────────────────────────────────

@app.post("/ontologies")
def add_ontology(
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

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ttl", delete=False, encoding="utf-8"
    ) as f:
        f.write(ttl_content)
        tmp_path = f.name

    try:
        entry = register_ontology(
            catalog_dir=CATALOG_DIR,
            slug=slug,
            ttl_path=tmp_path,
            label=body.get("label", ""),
            description=body.get("description", ""),
            tags=body.get("tags", []),
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return {"registered": True, "entry": entry}


# ── MCP SSE mount (for MCP-compatible agents) ───────────────────────

try:
    from ontorag.ontology_mcp import create_ontology_mcp
    _mcp = create_ontology_mcp(CATALOG_DIR)

    # FastMCP exposes .sse_app() for mounting into any ASGI app
    if hasattr(_mcp, "sse_app"):
        app.mount("/mcp", _mcp.sse_app())
        _log.info("MCP SSE endpoint mounted at /mcp")
    else:
        _log.info("FastMCP version does not expose sse_app(); MCP mount skipped")
except Exception as exc:
    _log.info("MCP mount skipped: %s", exc)
