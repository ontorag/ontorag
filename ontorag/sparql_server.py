from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from rdflib import Graph
from rdflib.plugins.sparql.processor import SPARQLResult

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.sparql_server")

def _detect_query_kind(query: str) -> str:
    import re
    # Strip PREFIX declarations, then check the first keyword
    q = query.strip()
    q_no_prefix = re.sub(r"(?i)^\s*(PREFIX\s+\S+:\s*<[^>]*>\s*)+", "", q).strip().lower()
    for kw in ("select", "ask", "construct", "describe"):
        if q_no_prefix.startswith(kw):
            return kw
    return "unknown"

def _best_mime_for_select(accept: str) -> str:
    accept = (accept or "").lower()
    if "text/csv" in accept:
        return "text/csv"
    if "text/tab-separated-values" in accept or "tsv" in accept:
        return "text/tab-separated-values"
    if "application/sparql-results+xml" in accept or "application/xml" in accept:
        return "application/sparql-results+xml"
    return "application/sparql-results+json"

def _best_mime_for_graph(accept: str) -> str:
    accept = (accept or "").lower()
    if "application/ld+json" in accept or "json-ld" in accept:
        return "application/ld+json"
    if "application/rdf+xml" in accept or "rdf+xml" in accept:
        return "application/rdf+xml"
    if "application/n-triples" in accept or "n-triples" in accept:
        return "application/n-triples"
    return "text/turtle"

def _serialize_select(result: SPARQLResult, mime: str) -> Tuple[bytes, str]:
    if mime == "text/csv":
        return result.serialize(format="csv"), mime
    if mime == "text/tab-separated-values":
        return result.serialize(format="tsv"), mime
    if mime == "application/sparql-results+xml":
        return result.serialize(format="xml"), mime
    return result.serialize(format="json"), "application/sparql-results+json"

def _serialize_graph_result(graph: Graph, mime: str) -> Tuple[bytes, str]:
    if mime == "application/ld+json":
        return graph.serialize(format="json-ld"), mime
    if mime == "application/rdf+xml":
        return graph.serialize(format="xml"), mime
    if mime == "application/n-triples":
        return graph.serialize(format="nt"), mime
    return graph.serialize(format="turtle"), "text/turtle"

def _load_graph(ontology_ttl: str, instances_ttl: str) -> Graph:
    _log.info("Loading graph: onto=%s inst=%s", ontology_ttl, instances_ttl)
    g = Graph()
    g.parse(ontology_ttl, format="turtle")
    g.parse(instances_ttl, format="turtle")
    _log.info("Graph loaded: %d triples", len(g))
    return g

def create_app(
    ontology_ttl: Optional[str] = None,
    instances_ttl: Optional[str] = None,
    enable_cors: bool = True,
    cors_allow_origins: str = "*",
) -> FastAPI:
    """
    Local in-memory SPARQL endpoint for inspecting collected RDF (ontology + instances).
    """
    app = FastAPI(title="OntoRAG SPARQL Inspect Server", version="0.1.0")

    if enable_cors:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_allow_origins.split(","),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Resolve defaults from env if not provided
    onto = ontology_ttl or os.getenv("ONTOLOGY_TTL", "")
    inst = instances_ttl or os.getenv("INSTANCES_TTL", "")

    # Keep graph in app.state
    app.state.graph = Graph()
    if onto and inst and Path(onto).exists() and Path(inst).exists():
        app.state.graph = _load_graph(onto, inst)

    @app.get("/health")
    def health():
        g: Graph = app.state.graph
        return {"ok": True, "triples": len(g)}

    @app.get("/stats")
    def stats():
        g: Graph = app.state.graph
        res = g.query("SELECT (COUNT(*) AS ?triples) WHERE { ?s ?p ?o }")
        triples = int(list(res)[0][0])
        return {"triples": triples}

    @app.post("/reload")
    async def reload_graph(request: Request):
        g: Graph
        body = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()

        onto2 = body.get("ontology_ttl") or os.getenv("ONTOLOGY_TTL")
        inst2 = body.get("instances_ttl") or os.getenv("INSTANCES_TTL")

        if not onto2 or not inst2:
            raise HTTPException(status_code=400, detail="Provide ontology_ttl & instances_ttl or set env vars.")
        if not Path(onto2).exists():
            raise HTTPException(status_code=400, detail=f"Ontology file not found: {onto2}")
        if not Path(inst2).exists():
            raise HTTPException(status_code=400, detail=f"Instances file not found: {inst2}")

        app.state.graph = _load_graph(onto2, inst2)
        return {"ok": True, "triples": len(app.state.graph)}

    @app.get("/sparql")
    async def sparql_get(query: str, request: Request):
        return await _sparql_execute(query, request)

    @app.post("/sparql")
    async def sparql_post(request: Request):
        ct = request.headers.get("content-type", "").lower()

        if "application/x-www-form-urlencoded" in ct:
            form = await request.form()
            query = form.get("query")
        elif "application/sparql-query" in ct:
            query = (await request.body()).decode("utf-8", errors="replace")
        elif "application/json" in ct:
            data = await request.json()
            query = data.get("query")
        else:
            query = (await request.body()).decode("utf-8", errors="replace")

        if not query:
            raise HTTPException(status_code=400, detail="Missing SPARQL query")

        return await _sparql_execute(query, request)

    async def _sparql_execute(query: str, request: Request):
        g: Graph = app.state.graph
        kind = _detect_query_kind(query)
        accept = request.headers.get("accept", "*/*")
        _log.debug("SPARQL %s query (%d chars), accept=%s", kind, len(query), accept)

        try:
            if kind in ("select", "ask"):
                result = g.query(query)
                mime = _best_mime_for_select(accept)
                payload, out_mime = _serialize_select(result, mime)
                return Response(content=payload, media_type=out_mime)

            if kind in ("construct", "describe"):
                result = g.query(query)
                out_graph = result if isinstance(result, Graph) else getattr(result, "graph", None)
                if out_graph is None:
                    raise RuntimeError("Unexpected graph result type")
                mime = _best_mime_for_graph(accept)
                payload, out_mime = _serialize_graph_result(out_graph, mime)
                return Response(content=payload, media_type=out_mime)

            # fallback
            result = g.query(query)
            if isinstance(result, Graph):
                mime = _best_mime_for_graph(accept)
                payload, out_mime = _serialize_graph_result(result, mime)
                return Response(content=payload, media_type=out_mime)
            mime = _best_mime_for_select(accept)
            payload, out_mime = _serialize_select(result, mime)
            return Response(content=payload, media_type=out_mime)

        except Exception as e:
            raise HTTPException(status_code=400, detail=f"SPARQL error: {e}")

    return app
