# ontorag/alignment_normalizer.py
"""Convert alignment output back into the standard proposal format.

Alignment JSON uses keys like ``induced_name``, ``action``,
``baseline_name`` etc.  The rest of the pipeline (proposal_to_ttl,
schema_card_from_proposal) expects the standard proposal shape
(``name``, ``domain``, ``range``, ``description``, ``origin``).

Call :func:`normalize_alignment` on any dict that *might* be alignment
output.  If it already uses proposal format it is returned as-is.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _is_alignment(items: list) -> bool:
    """True when items look like alignment output (have ``induced_name``)."""
    return bool(items) and "induced_name" in items[0]


def _build_proposal_index(proposal: Optional[dict]) -> Dict[str, dict]:
    """Build {name: item} lookup from original proposal for descriptions."""
    if not proposal:
        return {}
    idx: Dict[str, dict] = {}
    for key in ("classes", "datatype_properties", "object_properties"):
        for item in proposal.get(key, []):
            name = item.get("name", "")
            if name:
                idx[name] = item
    return idx


def _resolve_name(item: dict, *, action_key: str = "action") -> str:
    """Pick the effective name: baseline_name on reuse, else induced_name."""
    action = item.get(action_key, "new")
    if action == "reuse" and item.get("baseline_name"):
        return item["baseline_name"]
    return item.get("induced_name", "")


def _resolve_origin(item: dict) -> str:
    """Pick origin: baseline_origin on reuse/extend, else 'induced'."""
    action = item.get("action", "new")
    if action in ("reuse", "extend") and item.get("baseline_origin"):
        return item["baseline_origin"]
    return "induced"


def normalize_alignment(
    data: dict,
    original_proposal: Optional[dict] = None,
) -> dict:
    """Convert alignment-format data to standard proposal format.

    If *data* is already in proposal format (items have ``name`` not
    ``induced_name``), it is returned unchanged.

    When *original_proposal* is provided, descriptions and range types
    are pulled from it (alignment output strips these).

    The ``_partial`` and ``warnings`` keys are preserved.
    """
    out: Dict[str, Any] = {}
    idx = _build_proposal_index(original_proposal)

    # ── classes ────────────────────────────────────────────────────────
    classes = data.get("classes", [])
    if _is_alignment(classes):
        norm_classes: List[Dict[str, Any]] = []
        for c in classes:
            induced = c.get("induced_name", "")
            orig = idx.get(induced, {})
            entry: Dict[str, Any] = {
                "name": _resolve_name(c),
                "description": orig.get("description", ""),
                "origin": _resolve_origin(c),
            }
            # Carry alignment metadata for consumers that need it
            action = c.get("action", "new")
            if action == "extend" and c.get("baseline_name"):
                entry["subclass_of"] = c["baseline_name"]
            norm_classes.append(entry)
        out["classes"] = norm_classes
    else:
        out["classes"] = classes

    # ── datatype properties ───────────────────────────────────────────
    dt_props = data.get("datatype_properties", [])
    if _is_alignment(dt_props):
        norm_dt: List[Dict[str, Any]] = []
        for p in dt_props:
            induced = p.get("induced_name", "")
            orig = idx.get(induced, {})
            entry = {
                "name": _resolve_name(p),
                "domain": p.get("induced_domain", "") or orig.get("domain", ""),
                "range": orig.get("range", "any"),
                "description": orig.get("description", ""),
                "origin": _resolve_origin(p),
            }
            action = p.get("action", "new")
            if action == "extend" and p.get("baseline_name"):
                entry["subproperty_of"] = p["baseline_name"]
            norm_dt.append(entry)
        out["datatype_properties"] = norm_dt
    else:
        out["datatype_properties"] = dt_props

    # ── object properties ─────────────────────────────────────────────
    obj_props = data.get("object_properties", [])
    if _is_alignment(obj_props):
        norm_op: List[Dict[str, Any]] = []
        for p in obj_props:
            induced = p.get("induced_name", "")
            orig = idx.get(induced, {})
            entry = {
                "name": _resolve_name(p),
                "domain": p.get("induced_domain", "") or orig.get("domain", ""),
                "range": p.get("induced_range", "") or orig.get("range", ""),
                "description": orig.get("description", ""),
                "origin": _resolve_origin(p),
            }
            action = p.get("action", "new")
            if action == "extend" and p.get("baseline_name"):
                entry["subproperty_of"] = p["baseline_name"]
            norm_op.append(entry)
        out["object_properties"] = norm_op
    else:
        out["object_properties"] = obj_props

    # Pass through other keys (warnings, _partial, events, etc.)
    for k, v in data.items():
        if k not in out:
            out[k] = v

    return out
