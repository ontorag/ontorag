# ontorag/proposal_aggregator.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.proposal_aggregator")

def _key(name: str) -> str:
    return (name or "").strip().lower()

def _as_list(x):
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]

def _normalize_evidence(ev: Any, default_chunk_id: str = "") -> List[Dict[str, str]]:
    """
    Normalize evidence into: [{"chunk_id": "...", "quote": "..."}]
    Accepts:
      - list[dict]
      - dict
      - str
      - list[str]
      - anything else -> stringified
    """
    out: List[Dict[str, str]] = []

    for item in _as_list(ev):
        if item is None:
            continue

        # dict with fields
        if isinstance(item, dict):
            chunk_id = str(item.get("chunk_id") or default_chunk_id)
            quote = item.get("quote") or item.get("text") or item.get("snippet") or ""
            quote = str(quote).strip()
            if quote:
                out.append({"chunk_id": chunk_id, "quote": quote})
            continue

        # plain string -> treat as quote
        if isinstance(item, str):
            q = item.strip()
            if q:
                out.append({"chunk_id": default_chunk_id, "quote": q})
            continue

        # fallback
        q = str(item).strip()
        if q:
            out.append({"chunk_id": default_chunk_id, "quote": q})

    return out

def aggregate_chunk_proposals(chunk_props: List[Dict[str, Any]]) -> Dict[str, Any]:
    _log.info("Aggregating %d chunk proposals", len(chunk_props))

    classes: Dict[str, Dict[str, Any]] = {}
    dprops: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    oprops: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    events: Dict[str, Dict[str, Any]] = {}

    warnings: List[str] = []
    merges: List[dict] = []

    def merge_evidence(existing_ev: Any, new_ev: Any, default_chunk_id: str) -> List[Dict[str, str]]:
        existing = _normalize_evidence(existing_ev, default_chunk_id=default_chunk_id)
        new = _normalize_evidence(new_ev, default_chunk_id=default_chunk_id)

        seen = {(e["chunk_id"], e["quote"]) for e in existing}
        for e in new:
            t = (e["chunk_id"], e["quote"])
            if t not in seen:
                existing.append(e)
                seen.add(t)
        return existing

    for cp in chunk_props:
        cp_chunk_id = str(cp.get("chunk_id") or "")

        warnings.extend(_as_list(cp.get("warnings", [])))
        merges.extend(_as_list(cp.get("alias_or_merge_suggestions", [])))

        add = cp.get("proposed_additions", {}) or {}

        # ---- classes ----
        for c in _as_list(add.get("classes", [])):
            if not isinstance(c, dict):
                # LLM returned garbage; skip
                continue
            name = c.get("name")
            if not name:
                continue
            k = _key(name)
            if k not in classes:
                classes[k] = {
                    "name": name,
                    "description": c.get("description", "") or "",
                    "evidence": _normalize_evidence(c.get("evidence", []), default_chunk_id=cp_chunk_id)
                }
            else:
                # merge description (prefer non-empty / longer)
                old_desc = classes[k].get("description", "") or ""
                new_desc = c.get("description", "") or ""
                if new_desc and (not old_desc or len(new_desc) > len(old_desc)):
                    classes[k]["description"] = new_desc

                classes[k]["evidence"] = merge_evidence(
                    classes[k].get("evidence", []),
                    c.get("evidence", []),
                    default_chunk_id=cp_chunk_id
                )

        # ---- datatype properties ----
        for p in _as_list(add.get("datatype_properties", [])):
            if not isinstance(p, dict):
                continue
            dom, name, rng = p.get("domain"), p.get("name"), p.get("range")
            if not dom or not name or not rng:
                continue
            k = (_key(dom), _key(name), _key(rng))
            if k not in dprops:
                dprops[k] = {
                    "name": name,
                    "domain": dom,
                    "range": rng,
                    "description": p.get("description", "") or "",
                    "evidence": _normalize_evidence(p.get("evidence", []), default_chunk_id=cp_chunk_id)
                }
            else:
                old_desc = dprops[k].get("description", "") or ""
                new_desc = p.get("description", "") or ""
                if new_desc and (not old_desc or len(new_desc) > len(old_desc)):
                    dprops[k]["description"] = new_desc

                dprops[k]["evidence"] = merge_evidence(
                    dprops[k].get("evidence", []),
                    p.get("evidence", []),
                    default_chunk_id=cp_chunk_id
                )

        # ---- object properties ----
        for p in _as_list(add.get("object_properties", [])):
            if not isinstance(p, dict):
                continue
            dom, name, rng = p.get("domain"), p.get("name"), p.get("range")
            if not dom or not name or not rng:
                continue
            k = (_key(dom), _key(name), _key(rng))
            if k not in oprops:
                oprops[k] = {
                    "name": name,
                    "domain": dom,
                    "range": rng,
                    "description": p.get("description", "") or "",
                    "evidence": _normalize_evidence(p.get("evidence", []), default_chunk_id=cp_chunk_id)
                }
            else:
                old_desc = oprops[k].get("description", "") or ""
                new_desc = p.get("description", "") or ""
                if new_desc and (not old_desc or len(new_desc) > len(old_desc)):
                    oprops[k]["description"] = new_desc

                oprops[k]["evidence"] = merge_evidence(
                    oprops[k].get("evidence", []),
                    p.get("evidence", []),
                    default_chunk_id=cp_chunk_id
                )

        # ---- events ----
        for ev in _as_list(add.get("events", [])):
            if not isinstance(ev, dict):
                continue
            name = ev.get("name")
            if not name:
                continue
            k = _key(name)
            if k not in events:
                events[k] = {
                    "name": name,
                    "actors": _as_list(ev.get("actors", [])),
                    "effects": _as_list(ev.get("effects", [])),
                    "description": ev.get("description", "") or "",
                    "evidence": _normalize_evidence(ev.get("evidence", []), default_chunk_id=cp_chunk_id)
                }
            else:
                events[k]["actors"] = sorted(set(events[k].get("actors", [])) | set(_as_list(ev.get("actors", []))))
                events[k]["effects"] = sorted(set(events[k].get("effects", [])) | set(_as_list(ev.get("effects", []))))
                old_desc = events[k].get("description", "") or ""
                new_desc = ev.get("description", "") or ""
                if new_desc and (not old_desc or len(new_desc) > len(old_desc)):
                    events[k]["description"] = new_desc

                events[k]["evidence"] = merge_evidence(
                    events[k].get("evidence", []),
                    ev.get("evidence", []),
                    default_chunk_id=cp_chunk_id
                )

    # dedup warnings stable
    warnings_out = []
    seen_w = set()
    for w in warnings:
        if not w:
            continue
        s = str(w).strip()
        if s and s not in seen_w:
            warnings_out.append(s)
            seen_w.add(s)

    _log.info(
        "Aggregation result: classes=%d dt_props=%d obj_props=%d events=%d warnings=%d",
        len(classes), len(dprops), len(oprops), len(events), len(warnings_out),
    )
    _log.debug("Classes: %s", [v["name"] for v in classes.values()])
    _log.debug("Datatype properties: %s", [v["name"] for v in dprops.values()])
    _log.debug("Object properties: %s", [v["name"] for v in oprops.values()])

    return {
        "classes": list(classes.values()),
        "datatype_properties": list(dprops.values()),
        "object_properties": list(oprops.values()),
        "events": list(events.values()),
        "merge_suggestions": merges,
        "warnings": warnings_out,
    }
