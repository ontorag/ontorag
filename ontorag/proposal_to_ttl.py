# proposal_to_ttl.py
from __future__ import annotations
from typing import Dict, Optional
from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD

from ontorag.alignment_normalizer import normalize_alignment
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


def _is_baseline(origin: str, prefixes: Dict[str, str]) -> bool:
    """A term is baseline-origin when its origin is a known ontology slug."""
    return bool(origin) and origin != "induced" and origin in prefixes


def proposal_to_ttl(
    agg: dict,
    biz_ns: str = "http://www.example.com/biz/",
    original_proposal: Optional[dict] = None,
    prefixes: Optional[Dict[str, str]] = None,
    local_prefix: str = "biz",
    baseline_card: Optional[dict] = None,
) -> Graph:
    """Convert a schema proposal or alignment result to an RDF graph.

    Accepts either format — alignment items are auto-normalized to standard
    proposal shape first via :func:`normalize_alignment`.

    When *prefixes* (a ``{slug: namespace}`` map from the ontology catalog) is
    given, terms that come from a baseline ontology are emitted with **their
    origin IRI** instead of a local copy. So an induced ``Magus`` aligned as a
    subclass of the baseline ``rpg`` class ``Character`` is exported as
    ``local:Magus rdfs:subClassOf rpg:Character`` (rather than
    ``local:Magus rdfs:subClassOf local:Character``). Reused baseline terms are
    referenced by their origin IRI and not re-declared locally.

    Without *prefixes* the behaviour is unchanged: every term is minted under
    *biz_ns*.
    """
    agg = normalize_alignment(agg, original_proposal=original_proposal)
    prefixes = prefixes or {}

    n_cls = len(agg.get("classes", []))
    n_dp = len(agg.get("datatype_properties", []))
    n_op = len(agg.get("object_properties", []))
    _log.info("Exporting to TTL: %d classes, %d dt_props, %d obj_props (ns=%s)", n_cls, n_dp, n_op, biz_ns)

    BIZ = Namespace(biz_ns)
    g = Graph()
    g.bind(local_prefix, BIZ)
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)

    # ── Pass 1: learn which term names belong to a baseline ontology ──────
    # A baseline class/property is revealed either by being reused (origin is a
    # baseline slug and it is not itself a local subclass) or by being the
    # parent of a local `extend` term (subclass_of / subproperty_of).
    base_class_ns: Dict[str, str] = {}
    base_prop_ns: Dict[str, str] = {}

    def _note_baseline(name: str, origin: str, target: Dict[str, str]) -> None:
        if name and _is_baseline(origin, prefixes):
            target[name] = prefixes[origin]

    # Pass 0: seed every baseline class/property name from the baseline card, so
    # even names referenced only as a domain/range (e.g. rpg:Actor) resolve.
    if baseline_card:
        for c in baseline_card.get("classes", []):
            _note_baseline(c.get("name", ""), c.get("origin", ""), base_class_ns)
        for key in ("datatype_properties", "object_properties"):
            for p in baseline_card.get(key, []):
                _note_baseline(p.get("name", ""), p.get("origin", ""), base_prop_ns)

    for c in agg.get("classes", []):
        origin = c.get("origin", "")
        if c.get("subclass_of"):
            _note_baseline(c["subclass_of"], origin, base_class_ns)      # parent is baseline
        elif _is_baseline(origin, prefixes):
            _note_baseline(c["name"], origin, base_class_ns)             # reused baseline class
    for key, tgt in (("datatype_properties", base_prop_ns), ("object_properties", base_prop_ns)):
        for p in agg.get(key, []):
            origin = p.get("origin", "")
            if p.get("subproperty_of"):
                _note_baseline(p["subproperty_of"], origin, base_prop_ns)
            elif _is_baseline(origin, prefixes):
                _note_baseline(p["name"], origin, base_prop_ns)

    used_slugs = set()

    def _class_iri(name: str) -> URIRef:
        ns = base_class_ns.get(name)
        if ns:
            used_slugs.add(ns)
            return URIRef(ns + name)
        return URIRef(str(BIZ) + name)

    def _prop_iri(name: str) -> URIRef:
        ns = base_prop_ns.get(name)
        if ns:
            used_slugs.add(ns)
            return URIRef(ns + name)
        return URIRef(str(BIZ) + name)

    def _is_reused_baseline(item: dict, sub_key: str, name_map: Dict[str, str]) -> bool:
        """The item IS a baseline term (reuse) — reference it, don't redeclare."""
        return not item.get(sub_key) and name_map.get(item.get("name", "")) is not None

    # ── Pass 2: emit ─────────────────────────────────────────────────────
    # classes
    for c in agg.get("classes", []):
        if _is_reused_baseline(c, "subclass_of", base_class_ns):
            continue  # defined in the baseline ontology; only referenced
        cls = _class_iri(c["name"])
        g.add((cls, RDF.type, OWL.Class))
        if c.get("description"):
            g.add((cls, RDFS.comment, Literal(c["description"])))
        if c.get("subclass_of"):
            g.add((cls, RDFS.subClassOf, _class_iri(c["subclass_of"])))

    # datatype properties
    for p in agg.get("datatype_properties", []):
        if _is_reused_baseline(p, "subproperty_of", base_prop_ns):
            continue
        prop = _prop_iri(p["name"])
        rng = _RANGE_MAP.get(p.get("range", "string").lower(), XSD.string)
        g.add((prop, RDF.type, OWL.DatatypeProperty))
        g.add((prop, RDFS.domain, _class_iri(p["domain"])))
        g.add((prop, RDFS.range, rng))
        if p.get("description"):
            g.add((prop, RDFS.comment, Literal(p["description"])))
        if p.get("subproperty_of"):
            g.add((prop, RDFS.subPropertyOf, _prop_iri(p["subproperty_of"])))

    # object properties
    for p in agg.get("object_properties", []):
        if _is_reused_baseline(p, "subproperty_of", base_prop_ns):
            continue
        prop = _prop_iri(p["name"])
        g.add((prop, RDF.type, OWL.ObjectProperty))
        g.add((prop, RDFS.domain, _class_iri(p["domain"])))
        g.add((prop, RDFS.range, _class_iri(p["range"])))
        if p.get("description"):
            g.add((prop, RDFS.comment, Literal(p["description"])))
        if p.get("subproperty_of"):
            g.add((prop, RDFS.subPropertyOf, _prop_iri(p["subproperty_of"])))

    # bind baseline prefixes that were actually used, so the TTL reads rpg:… etc.
    ns_to_slug = {ns: slug for slug, ns in prefixes.items()}
    for ns in used_slugs:
        slug = ns_to_slug.get(ns)
        if slug:
            g.bind(slug, Namespace(ns))

    _log.info("TTL graph built: %d triples", len(g))
    return g
