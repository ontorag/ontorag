# ontorag/ontology_extractor_openrouter.py
from __future__ import annotations
import json
import os
import time
from typing import List, Dict, Any

import requests

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

    r = requests.post(url, headers=headers, json=payload, timeout=90)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]

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

    for ch in chunks:
        user = _build_prompt(ch, schema_card)

        for attempt in range(3):
            try:
                data = _chat_json(system, user)
                out.append(data)
                break
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
        time.sleep(10)

    return out
