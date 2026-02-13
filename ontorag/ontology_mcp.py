# ontorag/ontology_mcp.py
"""
MCP server for the ontology catalog.

Exposes baseline ontologies as browsable, searchable, composable tools
that LLM agents can use to select and configure starting schemas.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastmcp import FastMCP

from ontorag.ontology_catalog import (
    load_catalog,
    register_ontology,
    ttl_to_schema_card,
    compose_baselines,
)
from ontorag.verbosity import get_logger

_log = get_logger("ontorag.ontology_mcp")


def create_ontology_mcp(catalog_dir: str) -> FastMCP:
    _log.info("Creating ontology catalog MCP (catalog=%s)", catalog_dir)
    app = FastMCP("ontorag-ontology-catalog")

    @app.tool()
    def list_ontologies() -> Dict[str, Any]:
        """List all registered baseline ontologies in the catalog."""
        _log.debug("tool:list_ontologies")
        catalog = load_catalog(catalog_dir)
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

    @app.tool()
    def inspect_ontology(slug: str) -> Dict[str, Any]:
        """
        Inspect a baseline ontology: returns all classes, datatype
        properties, and object properties in schema-card format.
        """
        _log.debug("tool:inspect_ontology slug=%s", slug)
        catalog = load_catalog(catalog_dir)
        entry = next(
            (e for e in catalog.get("ontologies", []) if e["slug"] == slug), None
        )
        if entry is None:
            return {"error": f"Ontology '{slug}' not found in catalog."}

        from pathlib import Path

        ttl_file = str(Path(catalog_dir) / entry["path"])
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

    @app.tool()
    def search_classes(query: str) -> Dict[str, Any]:
        """
        Search for classes across all registered ontologies.
        Matches class name or description (case-insensitive substring).
        """
        query_lower = query.strip().lower()
        if not query_lower:
            return {"error": "Query must not be empty."}

        from pathlib import Path

        catalog = load_catalog(catalog_dir)
        results: List[Dict[str, Any]] = []

        for entry in catalog.get("ontologies", []):
            ttl_file = str(Path(catalog_dir) / entry["path"])
            card = ttl_to_schema_card(
                ttl_file, entry["slug"], namespace=entry.get("namespace")
            )

            for cls in card.get("classes", []):
                name = cls.get("name", "")
                desc = cls.get("description", "")
                if query_lower in name.lower() or query_lower in desc.lower():
                    results.append({
                        "ontology": entry["slug"],
                        "class": name,
                        "description": desc,
                    })

        return {"query": query, "count": len(results), "results": results}

    @app.tool()
    def search_properties(query: str) -> Dict[str, Any]:
        """
        Search for properties (datatype + object) across all ontologies.
        Matches property name, domain, range, or description.
        """
        query_lower = query.strip().lower()
        if not query_lower:
            return {"error": "Query must not be empty."}

        from pathlib import Path

        catalog = load_catalog(catalog_dir)
        results: List[Dict[str, Any]] = []

        for entry in catalog.get("ontologies", []):
            ttl_file = str(Path(catalog_dir) / entry["path"])
            card = ttl_to_schema_card(
                ttl_file, entry["slug"], namespace=entry.get("namespace")
            )

            for p in card.get("datatype_properties", []) + card.get("object_properties", []):
                searchable = " ".join([
                    p.get("name", ""),
                    p.get("domain", ""),
                    p.get("range", ""),
                    p.get("description", ""),
                ]).lower()
                if query_lower in searchable:
                    results.append({
                        "ontology": entry["slug"],
                        "property": p["name"],
                        "domain": p.get("domain", ""),
                        "range": p.get("range", ""),
                        "description": p.get("description", ""),
                    })

        return {"query": query, "count": len(results), "results": results}

    @app.tool()
    def compose(slugs: List[str], target_namespace: str = "") -> Dict[str, Any]:
        """
        Compose multiple baseline ontologies into a single schema card.
        Pass a list of ontology slugs. Returns a merged schema card
        ready to use as the starting point for extraction.
        """
        _log.debug("tool:compose slugs=%s", slugs)
        ns = target_namespace or None
        card = compose_baselines(catalog_dir, slugs, target_namespace=ns)
        return {
            "baselines_used": slugs,
            "classes_count": len(card.get("classes", [])),
            "datatype_properties_count": len(card.get("datatype_properties", [])),
            "object_properties_count": len(card.get("object_properties", [])),
            "schema_card": card,
        }

    @app.tool()
    def add_ontology(
        slug: str,
        ttl_content: str,
        label: str = "",
        description: str = "",
        tags: List[str] = [],
    ) -> Dict[str, Any]:
        """
        Register a new baseline ontology by providing its TTL content.
        The ontology is saved to the catalog and becomes available for
        inspection, search, and composition.
        """
        _log.debug("tool:add_ontology slug=%s content_len=%d", slug, len(ttl_content))
        from pathlib import Path
        import tempfile

        # Write TTL content to a temp file, then register via catalog
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ttl", delete=False, encoding="utf-8"
        ) as f:
            f.write(ttl_content)
            tmp_path = f.name

        try:
            entry = register_ontology(
                catalog_dir=catalog_dir,
                slug=slug,
                ttl_path=tmp_path,
                label=label,
                description=description,
                tags=tags,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return {"registered": True, "entry": entry}

    return app
