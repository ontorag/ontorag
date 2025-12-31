# ontology_proposal.py
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Optional

class Evidence(BaseModel):
    chunk_id: str
    quote: str

class ProposedClass(BaseModel):
    name: str
    description: str = ""
    evidence: List[Evidence] = Field(default_factory=list)

class ProposedDatatypeProperty(BaseModel):
    name: str
    domain: str
    range: str
    description: str = ""
    evidence: List[Evidence] = Field(default_factory=list)

class ProposedObjectProperty(BaseModel):
    name: str
    domain: str
    range: str
    description: str = ""
    evidence: List[Evidence] = Field(default_factory=list)

class ProposedEvent(BaseModel):
    name: str
    actors: List[str] = Field(default_factory=list)
    effects: List[str] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)

class ChunkOntologyProposal(BaseModel):
    chunk_id: str
    proposed_additions: dict
    reuse_instead_of_create: List[dict] = Field(default_factory=list)
    alias_or_merge_suggestions: List[dict] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
