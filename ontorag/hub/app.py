# ontorag/hub/app.py
"""
OntoRAG Hub — web API that orchestrates the pipeline.

* GitHub OAuth login
* Ingest files → content-hash → chunk → store DTOs to user's GitHub repo
* Schema & instance extraction → artifacts to user's repo
* Ontology registry → centrally stored, powers dynamic MCP endpoints
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from ontorag.hub.auth import (
    GITHUB_CLIENT_ID,
    CurrentUser,
    create_session_token,
    github_exchange_code,
    github_get_user,
    require_user,
)
from ontorag.hub.github_storage import (
    ensure_repo,
    file_exists,
    read_file,
    write_file,
)
from ontorag.hub.models import (
    ExtractInstancesRequest,
    ExtractInstancesResult,
    ExtractSchemaRequest,
    ExtractSchemaResult,
    IngestResult,
    McpEndpoint,
    OntologySummary,
    PublishOntologyRequest,
    TokenResponse,
)
from ontorag.verbosity import setup_logging, get_logger

VERBOSITY = int(os.getenv("ONTORAG_VERBOSITY", "0"))
setup_logging(VERBOSITY)
_log = get_logger("ontorag.hub.app")

# ── App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="OntoRAG Hub",
    version="0.1.0",
    description=(
        "GitHub-authenticated API for the OntoRAG pipeline.  "
        "User data lives in private GitHub repos; ontologies are centrally shared."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Central ontology store — in-memory for now, backed by disk.
_ONTOLOGY_DIR = Path(os.getenv("HUB_ONTOLOGY_DIR", "./data/hub_ontologies"))


# =====================================================================
#  Auth routes
# =====================================================================

@app.get("/auth/login")
async def auth_login():
    """Redirect the user to GitHub's OAuth authorization page."""
    if not GITHUB_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GITHUB_CLIENT_ID not configured")
    return RedirectResponse(
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&scope=repo"
    )


@app.get("/auth/callback")
async def auth_callback(code: str = Query(...)):
    """Exchange the GitHub OAuth code for a Hub session JWT."""
    gh_token = await github_exchange_code(code)
    user = await github_get_user(gh_token)
    session_token = create_session_token(user, gh_token)
    _log.info("User authenticated: %s", user.login)
    return TokenResponse(access_token=session_token, user=user)


@app.get("/auth/me")
async def auth_me(user: CurrentUser = Depends(require_user)):
    """Return the current user profile."""
    gh_user = await github_get_user(user.gh_token)
    return gh_user


# =====================================================================
#  Health
# =====================================================================

@app.get("/")
def root():
    return {"service": "ontorag-hub", "status": "ok"}


@app.get("/health")
def health():
    return {"ok": True}


# =====================================================================
#  Ingest — upload file, hash, chunk, store to user's repo
# =====================================================================

@app.post("/api/ingest", response_model=IngestResult)
async def api_ingest(
    file: UploadFile = File(...),
    force: bool = Query(False, description="Re-ingest even if content was already processed"),
    user: CurrentUser = Depends(require_user),
):
    """
    Upload a document, content-hash it, chunk it, and store the DTOs
    in the user's private ``ontorag-data`` GitHub repo.
    """
    raw = await file.read()
    content_hash = hashlib.sha256(raw).hexdigest()
    doc_id = f"doc_{content_hash[:16]}"

    repo = await ensure_repo(user.gh_token, user.login)
    doc_path = f"data/dto/documents/{doc_id}.json"

    # Dedup check
    if not force and await file_exists(user.gh_token, repo, doc_path):
        _log.info("SKIP ingest for %s: %s already exists", user.login, doc_id)
        return IngestResult(
            document_id=doc_id,
            content_hash=content_hash,
            chunks=0,
            repo=repo,
            skipped=True,
        )

    # Write to a temp file so the existing extractor can work on it
    suffix = Path(file.filename or "upload").suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name

    try:
        from ontorag.extractor_ingest import extract_with_pageindex

        doc = extract_with_pageindex(tmp_path, mime=file.content_type)
        # Override the document_id to match our content-hash based one
        doc.document_id = doc_id
        doc.content_hash = content_hash
        doc.source_path = file.filename or "upload"
        for i, chunk in enumerate(doc.chunks):
            chunk.document_id = doc_id
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # Serialize and push to user's GitHub repo
    doc_meta = doc.model_dump()
    doc_meta["chunks"] = []
    await write_file(
        user.gh_token, repo, doc_path,
        json.dumps(doc_meta, ensure_ascii=False, indent=2),
        message=f"ontorag-hub: ingest {doc_id}",
    )

    chunks_lines = [
        json.dumps(ch.model_dump(), ensure_ascii=False)
        for ch in doc.chunks
    ]
    await write_file(
        user.gh_token, repo, f"data/dto/chunks/{doc_id}.jsonl",
        "\n".join(chunks_lines) + "\n",
        message=f"ontorag-hub: chunks for {doc_id}",
    )

    _log.info("Ingested %s for %s: %d chunks -> %s", doc_id, user.login, len(doc.chunks), repo)
    return IngestResult(
        document_id=doc_id,
        content_hash=content_hash,
        chunks=len(doc.chunks),
        repo=repo,
    )


# =====================================================================
#  Schema extraction — proposals to user repo, ontology to central
# =====================================================================

@app.post("/api/extract-schema", response_model=ExtractSchemaResult)
async def api_extract_schema(
    body: ExtractSchemaRequest,
    user: CurrentUser = Depends(require_user),
):
    """
    Run ontology induction on a previously ingested document.
    Proposals are stored in the user's GitHub repo.
    """
    repo = await ensure_repo(user.gh_token, user.login)
    chunks_path = f"data/dto/chunks/{body.document_id}.jsonl"

    chunks_raw = await read_file(user.gh_token, repo, chunks_path)
    if chunks_raw is None:
        raise HTTPException(status_code=404, detail=f"Chunks not found: {body.document_id}")

    chunks_list = [json.loads(line) for line in chunks_raw.strip().splitlines() if line.strip()]

    # Load schema card: from central store if slug provided, else empty
    card: Dict[str, Any] = {"classes": [], "datatype_properties": [], "object_properties": []}
    if body.schema_card_slug:
        card_path = _ONTOLOGY_DIR / body.schema_card_slug / "schema_card.json"
        if card_path.exists():
            card = json.loads(card_path.read_text(encoding="utf-8"))

    from ontorag.ontology_extractor_openrouter import extract_schema_chunk_proposals
    from ontorag.proposal_aggregator import aggregate_chunk_proposals

    chunk_proposals = extract_schema_chunk_proposals(chunks_list, card)
    aggregated = aggregate_chunk_proposals(chunk_proposals)

    proposal_path = f"data/proposals/{body.document_id}.schema.json"
    await write_file(
        user.gh_token, repo, proposal_path,
        json.dumps(aggregated, ensure_ascii=False, indent=2),
        message=f"ontorag-hub: schema proposal for {body.document_id}",
    )

    return ExtractSchemaResult(
        document_id=body.document_id,
        proposal_path=proposal_path,
        classes_proposed=len(aggregated.get("classes", [])),
        properties_proposed=(
            len(aggregated.get("datatype_properties", []))
            + len(aggregated.get("object_properties", []))
        ),
    )


# =====================================================================
#  Instance extraction — to user repo
# =====================================================================

@app.post("/api/extract-instances", response_model=ExtractInstancesResult)
async def api_extract_instances(
    body: ExtractInstancesRequest,
    user: CurrentUser = Depends(require_user),
):
    """
    Extract instances from a document and store the TTL in the user's repo.
    """
    repo = await ensure_repo(user.gh_token, user.login)
    chunks_path = f"data/dto/chunks/{body.document_id}.jsonl"

    chunks_raw = await read_file(user.gh_token, repo, chunks_path)
    if chunks_raw is None:
        raise HTTPException(status_code=404, detail=f"Chunks not found: {body.document_id}")

    chunks_list = [json.loads(line) for line in chunks_raw.strip().splitlines() if line.strip()]
    chunks_by_id = {c.get("chunk_id"): c for c in chunks_list if c.get("chunk_id")}

    card_path = _ONTOLOGY_DIR / body.schema_card_slug / "schema_card.json"
    if not card_path.exists():
        raise HTTPException(status_code=404, detail=f"Ontology not found: {body.schema_card_slug}")
    card = json.loads(card_path.read_text(encoding="utf-8"))

    from ontorag.instance_extractor_openrouter import extract_instance_chunk_proposals
    from ontorag.instances_to_ttl import instance_proposals_to_graph

    proposals = extract_instance_chunk_proposals(chunks_list, card)
    ns = card.get("namespace", "http://www.example.com/biz/")
    g = instance_proposals_to_graph(chunks_by_id, proposals, namespace=ns)
    ttl_content = g.serialize(format="turtle")

    instances_path = f"data/instances/{body.document_id}.instances.ttl"
    await write_file(
        user.gh_token, repo, instances_path,
        ttl_content,
        message=f"ontorag-hub: instances for {body.document_id}",
    )

    return ExtractInstancesResult(
        document_id=body.document_id,
        instances_path=instances_path,
        triples=len(g),
    )


# =====================================================================
#  Central ontology registry
# =====================================================================

@app.get("/api/ontologies", response_model=List[OntologySummary])
async def api_list_ontologies():
    """List all centrally registered ontologies."""
    _ONTOLOGY_DIR.mkdir(parents=True, exist_ok=True)
    results: List[OntologySummary] = []
    for entry in sorted(_ONTOLOGY_DIR.iterdir()):
        meta_path = entry / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            results.append(OntologySummary(**meta))
    return results


@app.post("/api/ontologies", response_model=OntologySummary)
async def api_publish_ontology(
    body: PublishOntologyRequest,
    user: CurrentUser = Depends(require_user),
):
    """
    Publish a schema card as a centrally shared ontology.

    The schema card is stored on the Hub server.  It can be referenced
    by any user for extraction, and powers dynamic onto-mcp endpoints.
    """
    slug_dir = _ONTOLOGY_DIR / body.slug
    slug_dir.mkdir(parents=True, exist_ok=True)

    card = body.schema_card
    (slug_dir / "schema_card.json").write_text(
        json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    meta = OntologySummary(
        slug=body.slug,
        label=body.label or body.slug,
        description=body.description,
        namespace=card.get("namespace", ""),
        classes=len(card.get("classes", [])),
        properties=(
            len(card.get("datatype_properties", []))
            + len(card.get("object_properties", []))
        ),
        tags=body.tags,
        owner=user.login,
    )
    (slug_dir / "meta.json").write_text(
        meta.model_dump_json(indent=2), encoding="utf-8"
    )

    _log.info("Published ontology %s by %s", body.slug, user.login)
    return meta


@app.get("/api/ontologies/{slug}", response_model=Dict[str, Any])
async def api_get_ontology(slug: str):
    """Return the full schema card for a registered ontology."""
    card_path = _ONTOLOGY_DIR / slug / "schema_card.json"
    if not card_path.exists():
        raise HTTPException(status_code=404, detail=f"Ontology not found: {slug}")
    return json.loads(card_path.read_text(encoding="utf-8"))


# =====================================================================
#  Dynamic MCP provisioning
# =====================================================================

@app.get("/api/mcp/{slug}", response_model=McpEndpoint)
async def api_mcp_endpoint(slug: str):
    """
    Return the MCP endpoint info for an ontology.

    The onto-mcp is nearly volume-less — it only needs the schema card
    structure (classes, properties) to expose SPARQL-template tools.
    No user data is required.
    """
    card_path = _ONTOLOGY_DIR / slug / "schema_card.json"
    if not card_path.exists():
        raise HTTPException(status_code=404, detail=f"Ontology not found: {slug}")

    # In production this would point to a dynamically provisioned MCP
    # server.  For now we return the expected endpoint shape.
    base_url = os.getenv("HUB_BASE_URL", "http://localhost:9010")
    return McpEndpoint(
        slug=slug,
        url=f"{base_url}/mcp/{slug}/sse",
        tools=[
            "sparql_select",
            "sparql_construct",
            "describe",
            "list_by_class",
            "outgoing",
            "incoming",
        ],
    )


# =====================================================================
#  User documents (list what's in their repo)
# =====================================================================

@app.get("/api/documents")
async def api_list_documents(user: CurrentUser = Depends(require_user)):
    """List documents ingested by the authenticated user."""
    import httpx

    repo = f"{user.login}/ontorag-data"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/contents/data/dto/documents",
            headers={
                "Authorization": f"Bearer {user.gh_token}",
                "Accept": "application/vnd.github+json",
            },
        )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    files = resp.json()
    return [
        {"document_id": f["name"].replace(".json", ""), "path": f["path"]}
        for f in files
        if f["name"].endswith(".json")
    ]
