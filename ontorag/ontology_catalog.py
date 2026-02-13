# ontorag/ontology_catalog.py
"""
Ontology catalog: stores baseline OWL/TTL ontologies and converts them
to schema-card format on demand.

Storage layout:
  <catalog_dir>/
    catalog.json          # manifest of registered ontologies
    <slug>.ttl            # individual ontology files
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.ontology_catalog")

# ── XSD → schema-card range mapping ──────────────────────────────────

_XSD_RANGE_MAP = {
    str(XSD.string): "string",
    str(XSD.normalizedString): "string",
    str(XSD.token): "string",
    str(XSD.language): "string",
    str(XSD.Name): "string",
    str(XSD.anyURI): "string",
    str(XSD.integer): "integer",
    str(XSD.int): "integer",
    str(XSD.long): "integer",
    str(XSD.short): "integer",
    str(XSD.byte): "integer",
    str(XSD.nonNegativeInteger): "integer",
    str(XSD.positiveInteger): "integer",
    str(XSD.nonPositiveInteger): "integer",
    str(XSD.negativeInteger): "integer",
    str(XSD.unsignedInt): "integer",
    str(XSD.unsignedLong): "integer",
    str(XSD.decimal): "number",
    str(XSD.float): "number",
    str(XSD.double): "number",
    str(XSD.boolean): "boolean",
    str(XSD.date): "date",
    str(XSD.dateTime): "datetime",
    str(XSD.dateTimeStamp): "datetime",
    str(XSD.time): "string",
}


def _xsd_to_card_range(uri: Optional[str]) -> str:
    if uri is None:
        return "any"
    return _XSD_RANGE_MAP.get(str(uri), "any")


def _local_name(uri: str) -> str:
    """Extract the local name after the last # or /."""
    s = str(uri)
    idx = max(s.rfind("#"), s.rfind("/"))
    return s[idx + 1:] if idx >= 0 else s


def _first_literal(g: Graph, subj: URIRef, pred: URIRef) -> str:
    for obj in g.objects(subj, pred):
        return str(obj)
    return ""


# ── TTL → schema card conversion ─────────────────────────────────────

def ttl_to_schema_card(
    ttl_path: str,
    slug: str,
    namespace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Parse an OWL/RDFS Turtle file and return a schema-card-format dict.
    Every item gets ``origin`` set to *slug*.
    """
    _log.info("Parsing TTL %s (slug=%s)", ttl_path, slug)
    g = Graph()
    g.parse(ttl_path, format="turtle")
    _log.debug("Parsed %d triples from %s", len(g), ttl_path)

    # auto-detect namespace from the most common subject prefix
    if namespace is None:
        namespace = _guess_namespace(g)
        _log.debug("Auto-detected namespace: %s", namespace)

    classes: List[Dict[str, Any]] = []
    datatype_properties: List[Dict[str, Any]] = []
    object_properties: List[Dict[str, Any]] = []

    # ── Classes ──
    for cls_uri in sorted(set(g.subjects(RDF.type, OWL.Class)) |
                          set(g.subjects(RDF.type, RDFS.Class))):
        if not isinstance(cls_uri, URIRef):
            continue
        name = _local_name(cls_uri)
        if not name or name.startswith("_"):
            continue
        classes.append({
            "name": name,
            "description": _first_literal(g, cls_uri, RDFS.comment),
            "origin": slug,
        })

    # ── Datatype Properties ──
    for prop_uri in sorted(g.subjects(RDF.type, OWL.DatatypeProperty)):
        if not isinstance(prop_uri, URIRef):
            continue
        name = _local_name(prop_uri)
        domain = _first_literal(g, prop_uri, RDFS.domain)
        rng_raw = _first_literal(g, prop_uri, RDFS.range)

        domain_name = _local_name(domain) if domain else ""
        rng = _xsd_to_card_range(rng_raw) if rng_raw else "any"

        datatype_properties.append({
            "name": name,
            "domain": domain_name,
            "range": rng,
            "description": _first_literal(g, prop_uri, RDFS.comment),
            "origin": slug,
        })

    # ── Object Properties ──
    for prop_uri in sorted(g.subjects(RDF.type, OWL.ObjectProperty)):
        if not isinstance(prop_uri, URIRef):
            continue
        name = _local_name(prop_uri)
        domain = _first_literal(g, prop_uri, RDFS.domain)
        rng = _first_literal(g, prop_uri, RDFS.range)

        domain_name = _local_name(domain) if domain else ""
        range_name = _local_name(rng) if rng else ""

        object_properties.append({
            "name": name,
            "domain": domain_name,
            "range": range_name,
            "description": _first_literal(g, prop_uri, RDFS.comment),
            "origin": slug,
        })

    _log.info(
        "TTL->card for %s: classes=%d dt_props=%d obj_props=%d",
        slug, len(classes), len(datatype_properties), len(object_properties),
    )
    return {
        "namespace": namespace or "",
        "classes": classes,
        "datatype_properties": datatype_properties,
        "object_properties": object_properties,
        "events": [],
        "aliases": [],
        "warnings": [],
    }


def _guess_namespace(g: Graph) -> str:
    """Pick the most frequent subject namespace in the graph."""
    counts: Dict[str, int] = {}
    for s in g.subjects():
        if not isinstance(s, URIRef):
            continue
        uri = str(s)
        idx = max(uri.rfind("#"), uri.rfind("/"))
        if idx > 0:
            ns = uri[: idx + 1]
            counts[ns] = counts.get(ns, 0) + 1
    if not counts:
        return ""
    return max(counts, key=counts.get)


# ── Catalog manifest ─────────────────────────────────────────────────

def _catalog_path(catalog_dir: str) -> Path:
    return Path(catalog_dir) / "catalog.json"


def load_catalog(catalog_dir: str) -> Dict[str, Any]:
    p = _catalog_path(catalog_dir)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"ontologies": []}


def save_catalog(catalog_dir: str, catalog: Dict[str, Any]) -> None:
    p = _catalog_path(catalog_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")


def register_ontology(
    catalog_dir: str,
    slug: str,
    ttl_path: str,
    label: str = "",
    description: str = "",
    namespace: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Copy a TTL file into the catalog and register it in the manifest.
    Returns the catalog entry.
    """
    _log.info("Registering ontology: slug=%s from %s", slug, ttl_path)
    catalog = load_catalog(catalog_dir)
    dest = Path(catalog_dir) / f"{slug}.ttl"
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Copy TTL content
    src_content = Path(ttl_path).read_text(encoding="utf-8")
    dest.write_text(src_content, encoding="utf-8")
    _log.debug("Copied TTL to %s (%d chars)", dest, len(src_content))

    # Auto-detect namespace if not provided
    if namespace is None:
        card = ttl_to_schema_card(str(dest), slug)
        namespace = card.get("namespace", "")

    entry = {
        "slug": slug,
        "label": label or slug,
        "namespace": namespace,
        "description": description,
        "path": f"{slug}.ttl",
        "tags": tags or [],
    }

    # Replace existing entry with same slug, or append
    catalog["ontologies"] = [
        e for e in catalog.get("ontologies", []) if e.get("slug") != slug
    ]
    catalog["ontologies"].append(entry)
    catalog["ontologies"].sort(key=lambda e: e.get("slug", ""))

    save_catalog(catalog_dir, catalog)
    _log.info("Registered ontology %s (namespace=%s)", slug, namespace)
    return entry


def compose_baselines(
    catalog_dir: str,
    slugs: List[str],
    target_namespace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Merge multiple baseline ontologies into a single schema card.
    Classes/properties from each baseline carry their respective ``origin``.
    """
    from ontorag.schema_card import _ensure_schema_card_defaults

    _log.info("Composing baselines: %s", slugs)
    merged = _ensure_schema_card_defaults({})
    if target_namespace:
        merged["namespace"] = target_namespace

    catalog = load_catalog(catalog_dir)
    slug_to_entry = {e["slug"]: e for e in catalog.get("ontologies", [])}

    seen_classes: Dict[str, Dict[str, Any]] = {}
    seen_dprops: Dict[tuple, Dict[str, Any]] = {}
    seen_oprops: Dict[tuple, Dict[str, Any]] = {}

    for slug in slugs:
        entry = slug_to_entry.get(slug)
        if entry is None:
            merged["warnings"].append(f"Baseline '{slug}' not found in catalog.")
            continue

        ttl_file = str(Path(catalog_dir) / entry["path"])
        card = ttl_to_schema_card(ttl_file, slug, namespace=entry.get("namespace"))

        for c in card.get("classes", []):
            k = c["name"].lower()
            if k not in seen_classes:
                seen_classes[k] = c

        for p in card.get("datatype_properties", []):
            k = (p["domain"].lower(), p["name"].lower(), p["range"].lower())
            if k not in seen_dprops:
                seen_dprops[k] = p

        for p in card.get("object_properties", []):
            k = (p["domain"].lower(), p["name"].lower(), p["range"].lower())
            if k not in seen_oprops:
                seen_oprops[k] = p

    _log.info(
        "Composition result: classes=%d dt_props=%d obj_props=%d",
        len(seen_classes), len(seen_dprops), len(seen_oprops),
    )

    merged["classes"] = sorted(seen_classes.values(), key=lambda x: x["name"].lower())
    merged["datatype_properties"] = sorted(
        seen_dprops.values(),
        key=lambda x: (x["domain"].lower(), x["name"].lower(), x["range"].lower()),
    )
    merged["object_properties"] = sorted(
        seen_oprops.values(),
        key=lambda x: (x["domain"].lower(), x["name"].lower(), x["range"].lower()),
    )

    return merged
