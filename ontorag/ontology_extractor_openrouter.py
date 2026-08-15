# ontorag/ontology_extractor_openrouter.py
from __future__ import annotations
import json
import time
from typing import Callable, List, Dict, Any, Optional

ChunkProgressCallback = Callable[[int, int, str, Dict[str, Any]], None]
"""(chunk_index, total_chunks, chunk_id, proposal_or_error) → None"""

import requests

from ontorag.verbosity import get_logger
from ontorag import llm_config
from ontorag.card_slim import slim_card
from ontorag.parallel import map_chunks, get_concurrency

_log = get_logger("ontorag.ontology_extractor")


def _build_prompt(chunk: Dict[str, Any], schema_card: Dict[str, Any]) -> str:
    # With --slim-card, show only the chunk-relevant slice of the current card;
    # align-schema reconciles anything re-proposed against the full baseline later.
    # A no-op by default.
    card = slim_card(schema_card, chunk.get("text", ""))
    return f"""
You are an ontology induction engine.

CHUNK DTO (JSON):
{json.dumps(chunk, ensure_ascii=False)}

CURRENT SCHEMA CARD (JSON):
{json.dumps(card, ensure_ascii=False)}

Return STRICT JSON with this structure:
{{
  "chunk_id": "{chunk.get("chunk_id","")}",
  "proposed_additions": {{
    "classes": [],
    "datatype_properties": [],
    "object_properties": [],
    "events": []
  }},
  "reuse_instead_of_create": [],
  "alias_or_merge_suggestions": [],
  "warnings": []
}}

Rules:
- Do not invent facts not grounded in the chunk.
- Use precise, domain-specific class names that capture the concept accurately.
  Do NOT use overly broad names that collapse distinct concepts into one class.
- Propose a new class whenever a concept has meaningfully different attributes,
  roles, or domain significance from existing classes — even if they share a
  parent concept. Err on the side of more classes, not fewer.
- Only add an item to "reuse_instead_of_create" when the existing schema item
  is truly identical (same extension AND intension). A specialization or
  domain-specific variant should be a new class, not a reuse.
- Merge suggestions in "alias_or_merge_suggestions" should be rare and only
  for genuine synonyms (same meaning, different label).
- Evidence quotes must be short (<= 25 words) and copied verbatim from the chunk.
- Output JSON only. No extra text.
""".strip()

def _chat_json(system: str, user: str) -> Dict[str, Any]:
    key = llm_config.api_key()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set (set it in the environment or pass --api-key)")

    url = f"{llm_config.base_url()}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": llm_config.site_url(),
        "X-Title": llm_config.app_name(),
    }
    payload = {
        "model": llm_config.model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }

    _log.debug("API request: model=%s prompt_len=%d", llm_config.model(), len(user))
    r = requests.post(url, headers=headers, json=payload, timeout=180)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    _log.debug("API response: %d chars", len(content))

    # robust JSON parse (strip fences if present)
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1].strip()
        if content.startswith("json"):
            content = content[4:].strip()

    return json.loads(content)

def extract_schema_chunk_proposals(
    chunks: List[Dict[str, Any]],
    schema_card: Dict[str, Any],
    on_chunk_done: Optional[ChunkProgressCallback] = None,
    concurrency: Optional[int] = None,
) -> List[Dict[str, Any]]:
    system = "You are a careful ontology induction engine. Output JSON only."
    total = len(chunks)
    workers = concurrency if concurrency is not None else get_concurrency()

    _log.info("Schema extraction: %d chunks, model=%s, concurrency=%d", total, llm_config.model(), workers)

    def _work(i: int, ch: Dict[str, Any]) -> Dict[str, Any]:
        chunk_id = ch.get("chunk_id", f"#{i}")
        _log.info("  [%d/%d] Processing chunk %s", i + 1, total, chunk_id)
        user = _build_prompt(ch, schema_card)
        for attempt in range(3):
            try:
                data = _chat_json(system, user)
                adds = data.get("proposed_additions") or {}
                # tolerate null-valued list fields (models sometimes emit "classes": null)
                _log.debug("  -> proposals: classes=%d dt_props=%d obj_props=%d",
                           len(adds.get("classes") or []), len(adds.get("datatype_properties") or []),
                           len(adds.get("object_properties") or []))
                return data
            except Exception as e:
                _log.info("  Retry %d/3 for chunk %s: %s", attempt + 1, chunk_id, e)
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))

    def _on_done(i: int, data: Dict[str, Any]) -> None:
        if on_chunk_done:
            chunk_id = chunks[i].get("chunk_id", f"#{i}")
            on_chunk_done(i, total, chunk_id, data)

    out = map_chunks(chunks, _work, on_done=_on_done, concurrency=workers)
    _log.info("Schema extraction complete: %d proposals from %d chunks", len(out), total)
    return out
