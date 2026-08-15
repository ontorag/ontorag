from __future__ import annotations
import json
import time
from typing import List, Dict, Any, Optional

import requests

from ontorag.verbosity import get_logger
from ontorag import llm_config
from ontorag.card_slim import slim_card
from ontorag.parallel import map_chunks, get_concurrency

_log = get_logger("ontorag.instance_extractor")


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1].strip()
        if s.startswith("json"):
            s = s[4:].strip()
    return s

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
    content = _strip_fences(content)
    return json.loads(content)

def build_instance_prompt(chunk_dto: Dict[str, Any], schema_card: Dict[str, Any]) -> str:
    # Optionally prune the card to chunk-relevant terms (--slim-card); a no-op by default.
    card = slim_card(schema_card, chunk_dto.get("text", ""))
    schema_slim = {
        "namespace": card.get("namespace"),
        "classes": card.get("classes", []),
        "datatype_properties": card.get("datatype_properties", []),
        "object_properties": card.get("object_properties", []),
        "aliases": card.get("aliases", []),
    }

    return f"""
You are an information extraction engine grounded in a known ontology.

You receive:
- A CHUNK DTO (text + provenance)
- A SCHEMA CARD (classes + properties + relations)

Task:
Extract instance candidates mentioned in the chunk and express them as STRICT JSON.
Use ONLY class/property/relation names that exist in the schema card.
If the chunk mentions a concept not representable with the current schema, add it to "warnings" (do not invent schema).

CHUNK DTO (JSON):
{json.dumps(chunk_dto, ensure_ascii=False)}

SCHEMA CARD (JSON):
{json.dumps(schema_slim, ensure_ascii=False)}

OUTPUT (STRICT JSON):
{{
  "chunk_id": "{chunk_dto.get("chunk_id","")}",
  "instances": [
    {{
      "class": "ClassName",
      "id_hint": "short stable identifier if present in text, else empty",
      "label": "human name if present, else empty",
      "attributes": {{
        "datatypePropertyName": "string/number/bool/date as text",
        "...": "..."
      }},
      "relations": [
        {{
          "predicate": "objectPropertyName",
          "target_class": "ClassName",
          "target_label": "name if present",
          "target_id_hint": "id if present"
        }}
      ],
      "mentions": [
        {{
          "quote": "copy <= 25 words from the chunk",
          "offset_start": null,
          "offset_end": null
        }}
      ]
    }}
  ],
  "warnings": []
}}

Rules:
- Do not invent entities. Only extract what is clearly present in the chunk.
- Use the MOST SPECIFIC class available in the schema card.
  Do not assign a broad parent class when a more precise subclass applies.
- If an entity could belong to multiple classes (e.g. both a generic and a
  specific one), use the most specific class only.
- Keep quotes short and verbatim from the chunk.
- Prefer generic IDs if present (e.g., '#123', 'BG-01'); otherwise leave id_hint empty.
- Use schema names exactly as in schema card (case-sensitive).
- Output JSON only (no markdown, no commentary).
""".strip()

def extract_instance_chunk_proposals(
    chunks: List[Dict[str, Any]],
    schema_card: Dict[str, Any],
    max_retries: int = 3,
    concurrency: Optional[int] = None,
) -> List[Dict[str, Any]]:
    system = "You extract structured instances grounded in a provided ontology. Output JSON only."
    total = len(chunks)
    workers = concurrency if concurrency is not None else get_concurrency()

    _log.info("Instance extraction: %d chunks, model=%s, concurrency=%d", total, llm_config.model(), workers)

    def _work(i: int, ch: Dict[str, Any]) -> Dict[str, Any]:
        chunk_id = ch.get("chunk_id", f"#{i}")
        _log.info("  [%d/%d] Processing chunk %s", i + 1, total, chunk_id)
        user = build_instance_prompt(ch, schema_card)
        for attempt in range(max_retries):
            try:
                data = _chat_json(system, user)
                _log.debug("  -> extracted %d instances", len(data.get("instances") or []))
                return data
            except Exception as e:
                _log.info("  Retry %d/%d for chunk %s: %s", attempt + 1, max_retries, chunk_id, e)
                if attempt == max_retries - 1:
                    raise
                time.sleep(1.5 * (attempt + 1))

    out = map_chunks(chunks, _work, concurrency=workers)
    _log.info("Instance extraction complete: %d proposals from %d chunks", len(out), total)
    return out
