from __future__ import annotations
import os
import requests
from pathlib import Path

BLAZEGRAPH_ENDPOINT = os.getenv("BLAZEGRAPH_ENDPOINT", "http://localhost:9999/blazegraph/namespace/ontorag/sparql")

def blazegraph_sparql_update(update_query: str) -> None:
    r = requests.post(
        BLAZEGRAPH_ENDPOINT,
        data={"update": update_query},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    r.raise_for_status()

def blazegraph_upload_ttl(ttl_path: str, graph_iri: str) -> None:
    ttl = Path(ttl_path).read_text(encoding="utf-8")
    
    update = f"""
    INSERT DATA {{
      GRAPH <{graph_iri}> {{
{ttl}
      }}
    }}
    """
    blazegraph_sparql_update(update)
