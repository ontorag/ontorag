# proposal_aggregator.py
from __future__ import annotations
from typing import List, Dict, Any
from collections import defaultdict

def _key(name: str) -> str:
    return name.strip().lower()

def aggregate_chunk_proposals(chunk_props: List[Dict[str, Any]]) -> Dict[str, Any]:
    classes = {}
    dprops = {}
    oprops = {}
    events = {}

    warnings = []
    merges = []

    def merge_evidence(existing, new_evs):
        seen = {(e["chunk_id"], e["quote"]) for e in existing}
        for e in new_evs:
            t = (e["chunk_id"], e["quote"])
            if t not in seen:
                existing.append(e)
                seen.add(t)

    for cp in chunk_props:
        warnings.extend(cp.get("warnings", []))
        merges.extend(cp.get("alias_or_merge_suggestions", []))

        add = cp.get("proposed_additions", {})

        for c in add.get("classes", []):
            k = _key(c["name"])
            if k not in classes:
                classes[k] = c
            else:
                if not classes[k].get("description") and c.get("description"):
                    classes[k]["description"] = c["description"]
                merge_evidence(classes[k].setdefault("evidence", []), c.get("evidence", []))

        for p in add.get("datatype_properties", []):
            k = (_key(p["domain"]), _key(p["name"]), _key(p["range"]))
            if k not in dprops:
                dprops[k] = p
            else:
                merge_evidence(dprops[k].setdefault("evidence", []), p.get("evidence", []))

        for p in add.get("object_properties", []):
            k = (_key(p["domain"]), _key(p["name"]), _key(p["range"]))
            if k not in oprops:
                oprops[k] = p
            else:
                merge_evidence(oprops[k].setdefault("evidence", []), p.get("evidence", []))

        for ev in add.get("events", []):
            k = _key(ev["name"])
            if k not in events:
                events[k] = ev
            else:
                merge_evidence(events[k].setdefault("evidence", []), ev.get("evidence", []))

    return {
        "classes": list(classes.values()),
        "datatype_properties": list(dprops.values()),
        "object_properties": list(oprops.values()),
        "events": list(events.values()),
        "merge_suggestions": merges,
        "warnings": list(dict.fromkeys(warnings))  
    }
