# ontorag/hub/models.py
"""Pydantic request / response models for the Hub API."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ── Auth ─────────────────────────────────────────────────────────────

class GitHubUser(BaseModel):
    """Minimal profile returned after OAuth."""
    login: str
    id: int
    avatar_url: str = ""
    name: Optional[str] = None
    email: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: GitHubUser


# ── Ingest ───────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    """Returned by the upload endpoint before chunking starts."""
    filename: str
    content_hash: str
    document_id: str
    already_exists: bool = False


class IngestResult(BaseModel):
    document_id: str
    content_hash: str
    chunks: int
    repo: str
    skipped: bool = False


# ── Schema extraction ────────────────────────────────────────────────

class ExtractSchemaRequest(BaseModel):
    document_id: str
    schema_card_slug: Optional[str] = Field(
        None, description="Slug of a registered ontology to use as starting schema card"
    )


class ExtractSchemaResult(BaseModel):
    document_id: str
    proposal_path: str
    classes_proposed: int = 0
    properties_proposed: int = 0


# ── Instance extraction ──────────────────────────────────────────────

class ExtractInstancesRequest(BaseModel):
    document_id: str
    schema_card_slug: str


class ExtractInstancesResult(BaseModel):
    document_id: str
    instances_path: str
    triples: int = 0


# ── Ontology registry ───────────────────────────────────────────────

class OntologySummary(BaseModel):
    slug: str
    label: str = ""
    description: str = ""
    namespace: str = ""
    classes: int = 0
    properties: int = 0
    tags: List[str] = Field(default_factory=list)
    owner: Optional[str] = None


class PublishOntologyRequest(BaseModel):
    slug: str
    schema_card: Dict[str, Any]
    label: str = ""
    description: str = ""
    tags: List[str] = Field(default_factory=list)


# ── MCP provisioning ────────────────────────────────────────────────

class McpEndpoint(BaseModel):
    slug: str
    url: str
    tools: List[str] = Field(default_factory=list)
