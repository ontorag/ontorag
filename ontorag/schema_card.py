# schema_card.py
from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime, timezone

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.schema_card")

DT_RANGES = {"string","number","integer","boolean","date","datetime","enum","any"}

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _norm(s: str) -> str:
    return (s or "").strip()

def _key_class(name: str) -> str:
    return _norm(name).lower()

def _key_prop(domain: str, name: str, rng: str) -> Tuple[str,str,str]:
    return (_norm(domain).lower(), _norm(name).lower(), _norm(rng).lower())

def _merge_desc(old: str, new: str) -> str:
    old = _norm(old)
    new = _norm(new)
    if old and new:
        return new if len(new) > len(old) else old
    return new or old

def _ensure_schema_card_defaults(schema: Dict[str, Any]) -> Dict[str, Any]:
    schema = dict(schema or {})
    schema.setdefault("version", _now_iso())
    schema.setdefault("namespace", "http://www.example.com/biz/")
    schema.setdefault("classes", [])
    schema.setdefault("datatype_properties", [])
    schema.setdefault("object_properties", [])
    schema.setdefault("events", [])
    schema.setdefault("aliases", [])
    schema.setdefault("warnings", [])
    return schema

def schema_card_from_proposal(previous_schema_card: Dict[str, Any],
                              aggregated_proposal: Dict[str, Any],
                              namespace: Optional[str] = None) -> Dict[str, Any]:
    """
    Deterministico:
    - Unisce classi/proprietà/relazioni
    - Deduplica
    - Normalizza range datatype
    - Accumula warnings + merge_suggestions -> aliases
    """
    prev = _ensure_schema_card_defaults(previous_schema_card)
    out = _ensure_schema_card_defaults({})

    out["version"] = _now_iso()
    out["namespace"] = namespace or prev.get("namespace") or out["namespace"]

    _log.info(
        "Merging schema card: prev classes=%d proposal classes=%d namespace=%s",
        len(prev.get("classes", [])),
        len(aggregated_proposal.get("classes", [])),
        out["namespace"],
    )

    # ---- CLASSES ----
    cls_map: Dict[str, Dict[str, Any]] = {}
    for c in prev.get("classes", []):
        k = _key_class(c.get("name",""))
        if not k:
            continue
        cls_map[k] = {
            "name": c.get("name"),
            "description": _norm(c.get("description","")),
            "origin": c.get("origin", ""),
        }

    for c in aggregated_proposal.get("classes", []):
        k = _key_class(c.get("name",""))
        if not k:
            continue
        if k not in cls_map:
            cls_map[k] = {
                "name": c.get("name"),
                "description": _norm(c.get("description","")),
                "origin": c.get("origin", "induced"),
            }
        else:
            cls_map[k]["description"] = _merge_desc(cls_map[k].get("description",""), c.get("description",""))

    out["classes"] = sorted(cls_map.values(), key=lambda x: (x["name"] or "").lower())

    # ---- DATATYPE PROPERTIES ----
    dt_map: Dict[Tuple[str,str,str], Dict[str, Any]] = {}
    for p in prev.get("datatype_properties", []):
        dom = p.get("domain","")
        name = p.get("name","")
        rng = p.get("range","any")
        k = _key_prop(dom, name, rng)
        if not k[0] or not k[1]:
            continue
        dt_map[k] = {
            "name": name,
            "domain": dom,
            "range": rng,
            "description": _norm(p.get("description","")),
            "origin": p.get("origin", ""),
        }

    for p in aggregated_proposal.get("datatype_properties", []):
        dom = p.get("domain","")
        name = p.get("name","")
        rng = (p.get("range","any") or "any").lower()
        if rng not in DT_RANGES:
            rng = "any"
        k = _key_prop(dom, name, rng)
        if not k[0] or not k[1]:
            continue

        if k not in dt_map:
            dt_map[k] = {
                "name": name,
                "domain": dom,
                "range": rng,
                "description": _norm(p.get("description","")),
                "origin": p.get("origin", "induced"),
            }
        else:
            dt_map[k]["description"] = _merge_desc(dt_map[k].get("description",""), p.get("description",""))

    out["datatype_properties"] = sorted(
        dt_map.values(),
        key=lambda x: (x["domain"].lower(), x["name"].lower(), x["range"].lower())
    )

    # ---- OBJECT PROPERTIES ----
    op_map: Dict[Tuple[str,str,str], Dict[str, Any]] = {}
    for p in prev.get("object_properties", []):
        dom = p.get("domain","")
        name = p.get("name","")
        rng = p.get("range","")
        k = _key_prop(dom, name, rng)
        if not k[0] or not k[1] or not k[2]:
            continue
        op_map[k] = {
            "name": name,
            "domain": dom,
            "range": rng,
            "description": _norm(p.get("description","")),
            "origin": p.get("origin", ""),
        }

    for p in aggregated_proposal.get("object_properties", []):
        dom = p.get("domain","")
        name = p.get("name","")
        rng = p.get("range","")
        k = _key_prop(dom, name, rng)
        if not k[0] or not k[1] or not k[2]:
            continue

        if k not in op_map:
            op_map[k] = {
                "name": name,
                "domain": dom,
                "range": rng,
                "description": _norm(p.get("description","")),
                "origin": p.get("origin", "induced"),
            }
        else:
            op_map[k]["description"] = _merge_desc(op_map[k].get("description",""), p.get("description",""))

    out["object_properties"] = sorted(
        op_map.values(),
        key=lambda x: (x["domain"].lower(), x["name"].lower(), x["range"].lower())
    )

    # ---- EVENTS ----
    ev_map: Dict[str, Dict[str, Any]] = {}
    for e in prev.get("events", []):
        k = _norm(e.get("name","")).lower()
        if not k:
            continue
        ev_map[k] = {
            "name": e.get("name"),
            "actors": list(e.get("actors", [])),
            "effects": list(e.get("effects", [])),
            "description": _norm(e.get("description","")) if "description" in e else "",
            "origin": e.get("origin", ""),
        }

    for e in aggregated_proposal.get("events", []):
        k = _norm(e.get("name","")).lower()
        if not k:
            continue
        if k not in ev_map:
            ev_map[k] = {
                "name": e.get("name"),
                "actors": list(e.get("actors", [])),
                "effects": list(e.get("effects", [])),
                "description": _norm(e.get("description","")) if "description" in e else "",
                "origin": e.get("origin", "induced"),
            }
        else:
            # merge set-like per actors/effects
            ev_map[k]["actors"] = sorted(set(ev_map[k]["actors"]) | set(e.get("actors", [])))
            ev_map[k]["effects"] = sorted(set(ev_map[k]["effects"]) | set(e.get("effects", [])))
            if "description" in e:
                ev_map[k]["description"] = _merge_desc(ev_map[k].get("description",""), e.get("description",""))

    out["events"] = sorted(ev_map.values(), key=lambda x: (x["name"] or "").lower())

    # ---- ALIASES / MERGES ----
    aliases = []
    seen_alias = set()

    def add_alias(names: List[str], rationale: str = ""):
        norm_names = [n.strip() for n in names if (n or "").strip()]
        key = tuple(sorted(n.lower() for n in norm_names))
        if not norm_names or key in seen_alias:
            return
        seen_alias.add(key)
        aliases.append({"names": norm_names, "rationale": _norm(rationale)})

    for a in prev.get("aliases", []):
        add_alias(a.get("names", []), a.get("rationale",""))

    for a in aggregated_proposal.get("merge_suggestions", []):
        add_alias(a.get("names", []), a.get("rationale",""))

    out["aliases"] = aliases

    # ---- WARNINGS ----
    warnings = list(prev.get("warnings", [])) + list(aggregated_proposal.get("warnings", []))
    out["warnings"] = list(dict.fromkeys([_norm(w) for w in warnings if _norm(w)]))

    # Sanity check: warn if property domain/range refers to a class not in the schema.
    # Use case-insensitive lookup to match the deduplication logic above.
    class_names_lower = {c["name"].lower() for c in out["classes"] if c.get("name")}
    for p in out["datatype_properties"]:
        if p["domain"] and p["domain"].lower() not in class_names_lower:
            out["warnings"].append(f"DatatypeProperty {p['name']} refers to unknown domain class {p['domain']}.")
    for p in out["object_properties"]:
        if p["domain"] and p["domain"].lower() not in class_names_lower:
            out["warnings"].append(f"ObjectProperty {p['name']} refers to unknown domain class {p['domain']}.")
        if p["range"] and p["range"].lower() not in class_names_lower:
            out["warnings"].append(f"ObjectProperty {p['name']} refers to unknown range class {p['range']}.")


    out["warnings"] = list(dict.fromkeys(out["warnings"]))

    _log.info(
        "Schema card built: classes=%d dt_props=%d obj_props=%d events=%d warnings=%d",
        len(out["classes"]),
        len(out["datatype_properties"]),
        len(out["object_properties"]),
        len(out["events"]),
        len(out["warnings"]),
    )
    if out["warnings"]:
        for w in out["warnings"]:
            _log.debug("  warning: %s", w)

    return out
