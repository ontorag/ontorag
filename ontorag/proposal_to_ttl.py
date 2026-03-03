# proposal_to_ttl.py
from __future__ import annotations
from typing import Dict, List, Optional
from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.proposal_to_ttl")

_RANGE_MAP = {
    "string": XSD.string,
    "number": XSD.decimal,
    "integer": XSD.integer,
    "boolean": XSD.boolean,
    "date": XSD.date,
    "datetime": XSD.dateTime,
    "enum": XSD.string,
    "any": XSD.string,
}


def _is_alignment(items: list) -> bool:
    """Return True if items look like alignment output (have induced_name)."""
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


def proposal_to_ttl(
    agg: dict,
    biz_ns: str = "http://www.example.com/biz/",
    original_proposal: Optional[dict] = None,
) -> Graph:
    """Convert a schema proposal or alignment result to an RDF graph.

    Accepts either:
    - A raw aggregated proposal (classes/properties with name/domain/range)
    - An alignment result (classes/properties with induced_name/action/baseline_name)

    When *original_proposal* is given alongside alignment data, descriptions
    and range types are pulled from the original proposal.
    """
    n_cls = len(agg.get("classes", []))
    n_dp = len(agg.get("datatype_properties", []))
    n_op = len(agg.get("object_properties", []))
    _log.info("Exporting to TTL: %d classes, %d dt_props, %d obj_props (ns=%s)", n_cls, n_dp, n_op, biz_ns)

    BIZ = Namespace(biz_ns)
    g = Graph()
    g.bind("biz", BIZ)
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)

    prop_idx = _build_proposal_index(original_proposal)

    # ── classes ────────────────────────────────────────────────────────
    classes = agg.get("classes", [])
    if _is_alignment(classes):
        for c in classes:
            action = c.get("action", "new")
            induced = c.get("induced_name", "")
            baseline = c.get("baseline_name", "")
            orig = prop_idx.get(induced, {})
            desc = orig.get("description", "")

            if action == "reuse" and baseline:
                # Use the baseline name — the induced concept maps directly
                cls = URIRef(str(BIZ) + baseline)
                g.add((cls, RDF.type, OWL.Class))
                if desc:
                    g.add((cls, RDFS.comment, Literal(desc)))
            elif action == "extend" and baseline:
                # Keep induced name, add subClassOf link to baseline
                cls = URIRef(str(BIZ) + induced)
                parent = URIRef(str(BIZ) + baseline)
                g.add((cls, RDF.type, OWL.Class))
                g.add((cls, RDFS.subClassOf, parent))
                if desc:
                    g.add((cls, RDFS.comment, Literal(desc)))
            else:
                # new — keep induced name
                cls = URIRef(str(BIZ) + induced)
                g.add((cls, RDF.type, OWL.Class))
                if desc:
                    g.add((cls, RDFS.comment, Literal(desc)))
    else:
        for c in classes:
            cls = URIRef(str(BIZ) + c["name"])
            g.add((cls, RDF.type, OWL.Class))
            if c.get("description"):
                g.add((cls, RDFS.comment, Literal(c["description"])))

    # ── datatype properties ───────────────────────────────────────────
    dt_props = agg.get("datatype_properties", [])
    if _is_alignment(dt_props):
        for p in dt_props:
            action = p.get("action", "new")
            induced = p.get("induced_name", "")
            baseline = p.get("baseline_name", "")
            orig = prop_idx.get(induced, {})
            desc = orig.get("description", "")
            dom_name = p.get("induced_domain", "") or orig.get("domain", "")
            rng_str = orig.get("range", "string")

            if action == "reuse" and baseline:
                prop_uri = URIRef(str(BIZ) + baseline)
            else:
                prop_uri = URIRef(str(BIZ) + induced)

            g.add((prop_uri, RDF.type, OWL.DatatypeProperty))
            if dom_name:
                g.add((prop_uri, RDFS.domain, URIRef(str(BIZ) + dom_name)))
            g.add((prop_uri, RDFS.range, _RANGE_MAP.get(rng_str.lower(), XSD.string)))
            if desc:
                g.add((prop_uri, RDFS.comment, Literal(desc)))

            if action == "extend" and baseline:
                parent = URIRef(str(BIZ) + baseline)
                g.add((prop_uri, RDFS.subPropertyOf, parent))
    else:
        for p in dt_props:
            prop_uri = URIRef(str(BIZ) + p["name"])
            dom = URIRef(str(BIZ) + p["domain"])
            rng = _RANGE_MAP.get(p.get("range", "string").lower(), XSD.string)

            g.add((prop_uri, RDF.type, OWL.DatatypeProperty))
            g.add((prop_uri, RDFS.domain, dom))
            g.add((prop_uri, RDFS.range, rng))
            if p.get("description"):
                g.add((prop_uri, RDFS.comment, Literal(p["description"])))

    # ── object properties ─────────────────────────────────────────────
    obj_props = agg.get("object_properties", [])
    if _is_alignment(obj_props):
        for p in obj_props:
            action = p.get("action", "new")
            induced = p.get("induced_name", "")
            baseline = p.get("baseline_name", "")
            orig = prop_idx.get(induced, {})
            desc = orig.get("description", "")
            dom_name = p.get("induced_domain", "") or orig.get("domain", "")
            rng_name = p.get("induced_range", "") or orig.get("range", "")

            if action == "reuse" and baseline:
                prop_uri = URIRef(str(BIZ) + baseline)
            else:
                prop_uri = URIRef(str(BIZ) + induced)

            g.add((prop_uri, RDF.type, OWL.ObjectProperty))
            if dom_name:
                g.add((prop_uri, RDFS.domain, URIRef(str(BIZ) + dom_name)))
            if rng_name:
                g.add((prop_uri, RDFS.range, URIRef(str(BIZ) + rng_name)))
            if desc:
                g.add((prop_uri, RDFS.comment, Literal(desc)))

            if action == "extend" and baseline:
                parent = URIRef(str(BIZ) + baseline)
                g.add((prop_uri, RDFS.subPropertyOf, parent))
    else:
        for p in obj_props:
            prop_uri = URIRef(str(BIZ) + p["name"])
            dom = URIRef(str(BIZ) + p["domain"])
            rng = URIRef(str(BIZ) + p["range"])

            g.add((prop_uri, RDF.type, OWL.ObjectProperty))
            g.add((prop_uri, RDFS.domain, dom))
            g.add((prop_uri, RDFS.range, rng))
            if p.get("description"):
                g.add((prop_uri, RDFS.comment, Literal(p["description"])))

    _log.info("TTL graph built: %d triples", len(g))
    return g
