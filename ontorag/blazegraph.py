from __future__ import annotations
import os
import requests
from pathlib import Path

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.blazegraph")

BLAZEGRAPH_ENDPOINT = os.getenv("BLAZEGRAPH_ENDPOINT", "http://localhost:9999/blazegraph/namespace/ontorag/sparql")

def blazegraph_sparql_update(update_query: str) -> None:
    _log.info("SPARQL UPDATE to %s (%d chars)", BLAZEGRAPH_ENDPOINT, len(update_query))
    _log.debug("Query:\n%s", update_query[:500])
    r = requests.post(
        BLAZEGRAPH_ENDPOINT,
        data={"update": update_query},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    r.raise_for_status()
    _log.debug("Blazegraph response: %d %s", r.status_code, r.reason)

def blazegraph_upload_ttl(ttl_path: str, graph_iri: str) -> None:
    ttl = Path(ttl_path).read_bytes()
    _log.info("Uploading TTL %s (%d bytes) to graph <%s>", ttl_path, len(ttl), graph_iri)

    # Use Blazegraph REST API for bulk loading instead of embedding raw TTL
    # in a SPARQL UPDATE string (which breaks on TTL containing curly braces).
    url = BLAZEGRAPH_ENDPOINT
    if "?" not in url:
        url += f"?context-uri={graph_iri}"
    else:
        url += f"&context-uri={graph_iri}"

    _log.debug("POST %s", url)
    r = requests.post(
        url,
        data=ttl,
        headers={"Content-Type": "application/x-turtle"},
        timeout=120,
    )
    r.raise_for_status()
    _log.debug("Blazegraph response: %d %s", r.status_code, r.reason)
