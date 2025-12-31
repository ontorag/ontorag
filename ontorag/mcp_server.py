from __future__ import annotations
from typing import Optional, Dict, Any

from fastmcp import FastMCP
from pydantic import BaseModel

from ontorag.mcp_backend import SparqlBackend

def create_mcp_app(backend: SparqlBackend) -> FastMCP:
    app = FastMCP("ontorag-mcp")

    @app.tool()
    def sparql_select(query: str) -> Dict[str, Any]:
        """Run a SPARQL SELECT/ASK query and return SPARQL Results JSON."""
        return backend.select(query)

    @app.tool()
    def sparql_construct(query: str, accept: str = "text/turtle") -> Dict[str, Any]:
        """Run a SPARQL CONSTRUCT/DESCRIBE and return RDF as text."""
        data = backend.construct(query, accept=accept)
        return {"content_type": accept, "data": data}

    @app.tool()
    def describe(iri: str, accept: str = "text/turtle") -> Dict[str, Any]:
        """DESCRIBE a resource by IRI."""
        q = f"DESCRIBE <{iri}>"
        data = backend.construct(q, accept=accept)
        return {"content_type": accept, "data": data}

    @app.tool()
    def list_by_class(class_iri: str, limit: int = 50) -> Dict[str, Any]:
        """List instances of a class."""
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
        q = f"SELECT ?p ?o WHERE {{ <{iri}> ?p ?o }} LIMIT {int(limit)}"
        return backend.select(q)

    @app.tool()
    def incoming(iri: str, limit: int = 100) -> Dict[str, Any]:
        """Incoming edges to a resource."""
        q = f"SELECT ?s ?p WHERE {{ ?s ?p <{iri}> }} LIMIT {int(limit)}"
        return backend.select(q)

    return app
