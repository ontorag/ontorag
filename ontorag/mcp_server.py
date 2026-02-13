from __future__ import annotations
import re
from typing import Optional, Dict, Any

from fastmcp import FastMCP
from pydantic import BaseModel

from ontorag.mcp_backend import SparqlBackend
from ontorag.verbosity import get_logger

_log = get_logger("ontorag.mcp_server")


def _sanitize_iri(iri: str) -> str:
    """Reject IRIs that could break SPARQL angle-bracket syntax."""
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.\-]*:', iri):
        raise ValueError(f"Invalid IRI scheme: {iri!r}")
    if '>' in iri or '<' in iri:
        raise ValueError(f"IRI contains invalid characters: {iri!r}")
    return iri


def create_mcp_app(backend: SparqlBackend) -> FastMCP:
    _log.info("Creating MCP app with backend %s", type(backend).__name__)
    app = FastMCP("ontorag-mcp")

    @app.tool()
    def sparql_select(query: str) -> Dict[str, Any]:
        """Run a SPARQL SELECT/ASK query and return SPARQL Results JSON."""
        _log.debug("tool:sparql_select query=%d chars", len(query))
        return backend.select(query)

    @app.tool()
    def sparql_construct(query: str, accept: str = "text/turtle") -> Dict[str, Any]:
        """Run a SPARQL CONSTRUCT/DESCRIBE and return RDF as text."""
        _log.debug("tool:sparql_construct query=%d chars accept=%s", len(query), accept)
        data = backend.construct(query, accept=accept)
        return {"content_type": accept, "data": data}

    @app.tool()
    def describe(iri: str, accept: str = "text/turtle") -> Dict[str, Any]:
        """DESCRIBE a resource by IRI."""
        _log.debug("tool:describe iri=%s", iri)
        iri = _sanitize_iri(iri)
        q = f"DESCRIBE <{iri}>"
        data = backend.construct(q, accept=accept)
        return {"content_type": accept, "data": data}

    @app.tool()
    def list_by_class(class_iri: str, limit: int = 50) -> Dict[str, Any]:
        """List instances of a class."""
        class_iri = _sanitize_iri(class_iri)
        q = f"""
        SELECT ?s ?label WHERE {{
          ?s a <{class_iri}> .
          OPTIONAL {{ ?s <http://www.w3.org/2000/01/rdf-schema#label> ?label }}
        }} LIMIT {int(limit)}
        """
        return backend.select(q)

    @app.tool()
    def outgoing(iri: str, limit: int = 100) -> Dict[str, Any]:
        """Outgoing edges from a resource."""
        iri = _sanitize_iri(iri)
        q = f"SELECT ?p ?o WHERE {{ <{iri}> ?p ?o }} LIMIT {int(limit)}"
        return backend.select(q)

    @app.tool()
    def incoming(iri: str, limit: int = 100) -> Dict[str, Any]:
        """Incoming edges to a resource."""
        iri = _sanitize_iri(iri)
        q = f"SELECT ?s ?p WHERE {{ ?s ?p <{iri}> }} LIMIT {int(limit)}"
        return backend.select(q)

    return app
