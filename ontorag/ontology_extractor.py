# ontology_extractor.py
from __future__ import annotations
from typing import Iterable, Dict, Any, List
import json
from ontology_proposal import ChunkOntologyProposal

class OntologyExtractor:
    def __init__(self, llm_call):
        """
        llm_call(prompt:str) -> dict JSON parsed
        (inietti tu: OpenAI, Ollama, ecc.)
        """
        self.llm_call = llm_call

    def build_prompt(self, chunk_dto: Dict[str, Any], schema_card: Dict[str, Any]) -> str:
        return f"""You are an ontology induction engine.

CHUNK DTO (JSON):
{json.dumps(chunk_dto, ensure_ascii=False)}

CURRENT SCHEMA CARD (JSON):
{json.dumps(schema_card, ensure_ascii=False)}

OUTPUT (STRICT JSON):
{{ ... same JSON schema as specified ... }}

RULES:
- Do not invent facts.
- Prefer generic concept names over examples.
- Reuse schema items when possible.
- Evidence quotes must be short (<= 25 words) and copied from the chunk.
- Output strictly valid JSON, no extra text.
"""

    def extract_chunk(self, chunk_dto: Dict[str, Any], schema_card: Dict[str, Any]) -> ChunkOntologyProposal:
        prompt = self.build_prompt(chunk_dto, schema_card)
        data = self.llm_call(prompt)
        return ChunkOntologyProposal.model_validate(data)

    def extract_document(self, chunks: Iterable[Dict[str, Any]], schema_card: Dict[str, Any]) -> List[ChunkOntologyProposal]:
        out = []
        for ch in chunks:
            out.append(self.extract_chunk(ch, schema_card))
        return out
