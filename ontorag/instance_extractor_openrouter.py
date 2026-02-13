from __future__ import annotations
import json
import os
import time
import hashlib
from typing import List, Dict, Any

import requests

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.instance_extractor")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
APP_NAME = os.getenv("OPENROUTER_APP_NAME", "OntoRAG")
SITE_URL = os.getenv("OPENROUTER_SITE_URL", "https://ontorag.github.io")

def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1].strip()
        if s.startswith("json"):
            s = s[4:].strip()
    return s

def _chat_json(system: str, user: str) -> Dict[str, Any]:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    url = f"{OPENROUTER_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": SITE_URL,
        "X-Title": APP_NAME,
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }

    _log.debug("API request: model=%s prompt_len=%d", OPENROUTER_MODEL, len(user))
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    _log.debug("API response: %d chars", len(content))
    content = _strip_fences(content)
    return json.loads(content)

def build_instance_prompt(chunk_dto: Dict[str, Any], schema_card: Dict[str, Any]) -> str:
    schema_slim = {
        "namespace": schema_card.get("namespace"),
        "classes": schema_card.get("classes", []),
        "datatype_properties": schema_card.get("datatype_properties", []),
        "object_properties": schema_card.get("object_properties", []),
        "aliases": schema_card.get("aliases", []),
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
- Do not invent entities. Only extract what is clearly present.
- Keep quotes short and verbatim from the chunk.
- Prefer generic IDs if present (e.g., '#123', 'BG-01'); otherwise leave id_hint empty.
- Use schema names exactly as in schema card (case-sensitive).
- Output JSON only (no markdown, no commentary).
""".strip()

def extract_instance_chunk_proposals(
    chunks: List[Dict[str, Any]],
    schema_card: Dict[str, Any],
    max_retries: int = 3
) -> List[Dict[str, Any]]:
    system = "You extract structured instances grounded in a provided ontology. Output JSON only."
    out: List[Dict[str, Any]] = []
    total = len(chunks)

    _log.info("Instance extraction: %d chunks, model=%s", total, OPENROUTER_MODEL)

    for i, ch in enumerate(chunks):
        chunk_id = ch.get("chunk_id", f"#{i}")
        _log.info("  [%d/%d] Processing chunk %s", i + 1, total, chunk_id)
        user = build_instance_prompt(ch, schema_card)

        for attempt in range(max_retries):
            try:
                data = _chat_json(system, user)
                n_inst = len(data.get("instances", []))
                _log.debug("  -> extracted %d instances", n_inst)
                out.append(data)
                break
            except Exception as e:
                _log.info("  Retry %d/%d for chunk %s: %s", attempt + 1, max_retries, chunk_id, e)
                if attempt == max_retries - 1:
                    raise
                time.sleep(1.5 * (attempt + 1))

        # Rate-limit between chunks to avoid hitting API limits
        if i < total - 1:
            _log.debug("  Rate-limit pause (10s)")
            time.sleep(10)

    _log.info("Instance extraction complete: %d proposals from %d chunks", len(out), total)
    return out
