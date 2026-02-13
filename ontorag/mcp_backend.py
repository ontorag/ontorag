# ontorag/mcp_backend.py
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict

import requests
from rdflib import Graph
from rdflib.plugins.sparql.processor import SPARQLResult

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.mcp_backend")


class SparqlBackend(ABC):
    """Abstract backend exposing select() and construct() over a SPARQL store."""

    @abstractmethod
    def select(self, query: str) -> Dict[str, Any]:
        """Run a SELECT/ASK query, return SPARQL Results JSON (as dict)."""

    @abstractmethod
    def construct(self, query: str, accept: str = "text/turtle") -> str:
        """Run a CONSTRUCT/DESCRIBE query, return serialised RDF as text."""


class LocalRdfBackend(SparqlBackend):
    """In-memory rdflib backend loaded from local TTL files."""

    def __init__(self, ontology_ttl: str, instances_ttl: str) -> None:
        _log.info("LocalRdfBackend: loading onto=%s inst=%s", ontology_ttl, instances_ttl)
        self._graph = Graph()
        self._graph.parse(ontology_ttl, format="turtle")
        self._graph.parse(instances_ttl, format="turtle")
        _log.info("LocalRdfBackend: loaded %d triples", len(self._graph))

    def select(self, query: str) -> Dict[str, Any]:
        result: SPARQLResult = self._graph.query(query)
        raw = result.serialize(format="json")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def construct(self, query: str, accept: str = "text/turtle") -> str:
        result = self._graph.query(query)
        out_graph = result if isinstance(result, Graph) else getattr(result, "graph", None)
        if out_graph is None:
            raise RuntimeError("Query did not return a graph result")

        fmt_map = {
            "text/turtle": "turtle",
            "application/ld+json": "json-ld",
            "application/rdf+xml": "xml",
            "application/n-triples": "nt",
        }
        fmt = fmt_map.get(accept, "turtle")
        data = out_graph.serialize(format=fmt)
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return data


class RemoteSparqlBackend(SparqlBackend):
    """Backend that proxies queries to a remote SPARQL endpoint."""

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint
        _log.info("RemoteSparqlBackend: endpoint=%s", endpoint)

    def select(self, query: str) -> Dict[str, Any]:
        r = requests.post(
            self._endpoint,
            data={"query": query},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/sparql-results+json",
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def construct(self, query: str, accept: str = "text/turtle") -> str:
        r = requests.post(
            self._endpoint,
            data={"query": query},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": accept,
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.text
