# ontorag/schema_alignment.py
"""
Align induced schema proposals against baseline ontologies.

Given an aggregated proposal (output of ``extract-schema``) and a baseline
schema card (output of ``init-schema-card``), uses the LLM to hypothesize
which induced classes / properties correspond to existing baseline items.

The result is an alignment JSON that ``build-schema-card`` can consume to
set correct ``origin`` values and link back to the source TTLs.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.schema_alignment")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
APP_NAME = os.getenv("OPENROUTER_APP_NAME", "OntoRAG")
SITE_URL = os.getenv("OPENROUTER_SITE_URL", "https://ontorag.github.io")

AlignProgressCallback = Callable[..., None]
"""(category, category_result, *, resumed=False) → None"""


# ── LLM helper ────────────────────────────────────────────────────────

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
        "temperature": 0.1,
    }

    _log.debug("API request: model=%s prompt_len=%d", OPENROUTER_MODEL, len(user))
    _log.debug("API prompt:\n%s", user)
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    _log.debug("API response: %d chars", len(content))
    _log.debug("API raw response:\n%s", content)

    content = content.strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1].strip()
        if content.startswith("json"):
            content = content[4:].strip()

    return json.loads(content)


# ── Prompt builders ───────────────────────────────────────────────────

def _summarize_baseline_classes(baseline: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract a compact list of baseline classes with origin."""
    out = []
    for c in baseline.get("classes", []):
        out.append({
            "name": c.get("name", ""),
            "description": c.get("description", ""),
            "origin": c.get("origin", ""),
        })
    return out


def _summarize_baseline_props(baseline: Dict[str, Any], key: str) -> List[Dict[str, str]]:
    """Extract a compact list of baseline properties with origin."""
    out = []
    for p in baseline.get(key, []):
        out.append({
            "name": p.get("name", ""),
            "domain": p.get("domain", ""),
            "range": p.get("range", ""),
            "description": p.get("description", ""),
            "origin": p.get("origin", ""),
        })
    return out


def _summarize_induced_classes(proposal: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract induced classes with descriptions."""
    out = []
    for c in proposal.get("classes", []):
        out.append({
            "name": c.get("name", ""),
            "description": c.get("description", ""),
        })
    return out


def _summarize_induced_props(proposal: Dict[str, Any], key: str) -> List[Dict[str, str]]:
    """Extract induced properties with descriptions."""
    out = []
    for p in proposal.get(key, []):
        out.append({
            "name": p.get("name", ""),
            "domain": p.get("domain", ""),
            "range": p.get("range", ""),
            "description": p.get("description", ""),
        })
    return out


_ALIGN_CLASSES_PROMPT = """\
You are an ontology alignment engine.

INDUCED CLASSES (newly extracted from documents):
{induced}

BASELINE CLASSES (from registered ontologies):
{baseline}

For each INDUCED class, decide:
- "reuse": the induced class IS semantically the same as a baseline class.
  The induced name should be replaced by the baseline name.
- "extend": the induced class is a specialization (subClassOf) of a baseline class.
  Keep the induced name but record the parent.
- "new": no meaningful match in the baselines.

Return STRICT JSON:
{{
  "alignments": [
    {{
      "induced_name": "InducedClass",
      "action": "reuse|extend|new",
      "baseline_name": "BaselineClass or empty if new",
      "baseline_origin": "ontology slug or empty if new",
      "confidence": "high|medium|low",
      "rationale": "Brief explanation"
    }}
  ]
}}

Rules:
- Every induced class must appear exactly once.
- Only match when there is genuine semantic overlap.
- Prefer "reuse" over "extend" when the concepts are truly equivalent.
- If uncertain, use "new" with low confidence.
- Output JSON only. No extra text.
"""

_ALIGN_PROPS_PROMPT = """\
You are an ontology alignment engine.

INDUCED {prop_label} (newly extracted from documents):
{induced}

BASELINE {prop_label} (from registered ontologies):
{baseline}

For each INDUCED property, decide:
- "reuse": the induced property IS semantically the same as a baseline property.
  The induced name should be replaced by the baseline name.
- "extend": the induced property is a specialization (subPropertyOf) of a baseline property.
  Keep the induced name but record the parent.
- "new": no meaningful match in the baselines.

Return STRICT JSON:
{{
  "alignments": [
    {{
      "induced_name": "inducedProp",
      "induced_domain": "InducedDomain",
      "induced_range": "InducedRange",
      "action": "reuse|extend|new",
      "baseline_name": "baselineProp or empty if new",
      "baseline_domain": "BaselineDomain or empty if new",
      "baseline_origin": "ontology slug or empty if new",
      "confidence": "high|medium|low",
      "rationale": "Brief explanation"
    }}
  ]
}}

Rules:
- Every induced property must appear exactly once.
- Only match when there is genuine semantic overlap in both meaning and domain/range fit.
- Prefer "reuse" over "extend" when the concepts are truly equivalent.
- If uncertain, use "new" with low confidence.
- Output JSON only. No extra text.
"""


# ── Core alignment ────────────────────────────────────────────────────

def _align_classes(
    proposal: Dict[str, Any],
    baseline: Dict[str, Any],
) -> Dict[str, Any]:
    """Align induced classes against baseline classes."""
    induced = _summarize_induced_classes(proposal)
    base = _summarize_baseline_classes(baseline)

    if not induced:
        return {"alignments": []}
    if not base:
        return {"alignments": [
            {
                "induced_name": c["name"],
                "action": "new",
                "baseline_name": "",
                "baseline_origin": "",
                "confidence": "high",
                "rationale": "No baselines to align against.",
            }
            for c in induced
        ]}

    prompt = _ALIGN_CLASSES_PROMPT.format(
        induced=json.dumps(induced, ensure_ascii=False, indent=2),
        baseline=json.dumps(base, ensure_ascii=False, indent=2),
    )

    system = "You are a careful ontology alignment engine. Output JSON only."
    return _chat_json(system, prompt)


def _align_properties(
    proposal: Dict[str, Any],
    baseline: Dict[str, Any],
    prop_key: str,
    prop_label: str,
) -> Dict[str, Any]:
    """Align induced properties against baseline properties."""
    induced = _summarize_induced_props(proposal, prop_key)
    base = _summarize_baseline_props(baseline, prop_key)

    if not induced:
        return {"alignments": []}
    if not base:
        return {"alignments": [
            {
                "induced_name": p["name"],
                "induced_domain": p["domain"],
                "induced_range": p["range"],
                "action": "new",
                "baseline_name": "",
                "baseline_domain": "",
                "baseline_origin": "",
                "confidence": "high",
                "rationale": "No baselines to align against.",
            }
            for p in induced
        ]}

    prompt = _ALIGN_PROPS_PROMPT.format(
        prop_label=prop_label,
        induced=json.dumps(induced, ensure_ascii=False, indent=2),
        baseline=json.dumps(base, ensure_ascii=False, indent=2),
    )

    system = "You are a careful ontology alignment engine. Output JSON only."
    return _chat_json(system, prompt)


# ── Public entry point ────────────────────────────────────────────────

FlushCallback = Callable[[Dict[str, Any]], None]
"""Called after each category with the full partial result so it can be flushed to disk."""


def align_schema(
    proposal: Dict[str, Any],
    baseline: Dict[str, Any],
    on_category_done: Optional[AlignProgressCallback] = None,
    on_flush: Optional[FlushCallback] = None,
    prior: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Align an induced schema proposal against a baseline schema card.

    Returns an alignment dict with three categories (classes,
    datatype_properties, object_properties), each containing a list
    of alignment entries with action/confidence/rationale.

    If *prior* is provided (e.g. from a partial earlier run), categories
    that already have alignments are skipped — no wasted LLM calls.
    """
    _log.info(
        "Aligning proposal (C=%d D=%d O=%d) against baseline (C=%d D=%d O=%d)",
        len(proposal.get("classes", [])),
        len(proposal.get("datatype_properties", [])),
        len(proposal.get("object_properties", [])),
        len(baseline.get("classes", [])),
        len(baseline.get("datatype_properties", [])),
        len(baseline.get("object_properties", [])),
    )

    categories = [
        ("classes", "classes", "DATATYPE PROPERTIES"),
        ("datatype_properties", "datatype_properties", "DATATYPE PROPERTIES"),
        ("object_properties", "object_properties", "OBJECT PROPERTIES"),
    ]

    result: Dict[str, Any] = {
        "classes": [],
        "datatype_properties": [],
        "object_properties": [],
        "warnings": [],
        "_partial": True,
    }

    for cat_key, prop_key, prop_label in categories:
        # ── Resume: skip categories already completed in a prior run ──
        if prior and len(prior.get(cat_key, [])) > 0:
            _log.info("  %s: reusing %d alignments from prior run", cat_key, len(prior[cat_key]))
            result[cat_key] = prior[cat_key]
            cat_result: Dict[str, Any] = {"alignments": prior[cat_key]}
            if on_category_done:
                on_category_done(cat_key, cat_result, resumed=True)
            continue

        induced_count = len(proposal.get(cat_key, []))
        if induced_count == 0:
            _log.info("  %s: nothing to align (0 induced)", cat_key)
            cat_result = {"alignments": []}
        else:
            _log.info("  %s: aligning %d induced items", cat_key, induced_count)
            for attempt in range(3):
                try:
                    if cat_key == "classes":
                        cat_result = _align_classes(proposal, baseline)
                    else:
                        cat_result = _align_properties(proposal, baseline, prop_key, prop_label)
                    break
                except Exception as e:
                    _log.info("  Retry %d/3 for %s: %s", attempt + 1, cat_key, e)
                    if attempt == 2:
                        cat_result = {"alignments": []}
                        result["warnings"].append(f"Alignment failed for {cat_key}: {e}")
                    else:
                        time.sleep(2 * (attempt + 1))

        alignments = cat_result.get("alignments", [])
        result[cat_key] = alignments

        if on_category_done:
            on_category_done(cat_key, cat_result)

        # Flush partial result so work-so-far is saved to disk
        if on_flush:
            on_flush(result)

        # Rate-limit pause between LLM calls
        if cat_key != "object_properties" and induced_count > 0:
            time.sleep(2)

    # Remove partial marker only when every category succeeded
    if not result["warnings"]:
        result.pop("_partial", None)

    # Compute summary counts
    for cat_key in ("classes", "datatype_properties", "object_properties"):
        items = result[cat_key]
        reuse = sum(1 for a in items if a.get("action") == "reuse")
        extend = sum(1 for a in items if a.get("action") == "extend")
        new = sum(1 for a in items if a.get("action") == "new")
        _log.info("  %s alignment: reuse=%d extend=%d new=%d", cat_key, reuse, extend, new)

    return result
