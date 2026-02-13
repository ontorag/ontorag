# ontorag/ontology_extractor_openrouter.py
from __future__ import annotations
import json
import os
import time
from typing import List, Dict, Any

import requests

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.ontology_extractor")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

APP_NAME = os.getenv("OPENROUTER_APP_NAME", "OntoRAG")
SITE_URL = os.getenv("OPENROUTER_SITE_URL", "https://ontorag.github.io")

def _build_prompt(chunk: Dict[str, Any], schema_card: Dict[str, Any]) -> str:
    return f"""
You are an ontology induction engine.

CHUNK DTO (JSON):
{json.dumps(chunk, ensure_ascii=False)}

CURRENT SCHEMA CARD (JSON):
{json.dumps(schema_card, ensure_ascii=False)}

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
- Do not invent facts.
- Prefer generic names over examples.
- Reuse existing schema items when possible.
- Evidence quotes must be short (<= 25 words) and copied from the chunk.
- Output JSON only. No extra text.
""".strip()

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
    r = requests.post(url, headers=headers, json=payload, timeout=90)
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

def extract_schema_chunk_proposals(chunks: List[Dict[str, Any]], schema_card: Dict[str, Any]) -> List[Dict[str, Any]]:
    system = "You are a careful ontology induction engine. Output JSON only."
    out: List[Dict[str, Any]] = []
    total = len(chunks)

    _log.info("Schema extraction: %d chunks, model=%s", total, OPENROUTER_MODEL)

    for i, ch in enumerate(chunks):
        chunk_id = ch.get("chunk_id", f"#{i}")
        _log.info("  [%d/%d] Processing chunk %s", i + 1, total, chunk_id)
        user = _build_prompt(ch, schema_card)

        for attempt in range(3):
            try:
                data = _chat_json(system, user)
                n_cls = len((data.get("proposed_additions") or {}).get("classes", []))
                n_dp = len((data.get("proposed_additions") or {}).get("datatype_properties", []))
                n_op = len((data.get("proposed_additions") or {}).get("object_properties", []))
                _log.debug("  -> proposals: classes=%d dt_props=%d obj_props=%d", n_cls, n_dp, n_op)
                out.append(data)
                break
            except Exception as e:
                _log.info("  Retry %d/3 for chunk %s: %s", attempt + 1, chunk_id, e)
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))

        if i < total - 1:
            _log.debug("  Rate-limit pause (10s)")
            time.sleep(10)

    _log.info("Schema extraction complete: %d proposals from %d chunks", len(out), total)
    return out
