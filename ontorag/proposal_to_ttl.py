# proposal_to_ttl.py
from __future__ import annotations
from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD

def proposal_to_ttl(agg: dict, biz_ns: str = "http://www.example.com/biz/") -> Graph:
    BIZ = Namespace(biz_ns)
    g = Graph()
    g.bind("biz", BIZ)
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)

    # classes
    for c in agg.get("classes", []):
        cls = URIRef(str(BIZ) + c["name"])
        g.add((cls, RDF.type, OWL.Class))
        if c.get("description"):
            g.add((cls, RDFS.comment, Literal(c["description"])))

    # datatype properties
    range_map = {
        "string": XSD.string,
        "number": XSD.decimal,
        "integer": XSD.integer,
        "boolean": XSD.boolean,
        "date": XSD.date,
        "datetime": XSD.dateTime,
        "enum": XSD.string,
        "any": XSD.string
    }

    for p in agg.get("datatype_properties", []):
        prop = URIRef(str(BIZ) + p["name"])
        dom = URIRef(str(BIZ) + p["domain"])
        rng = range_map.get(p.get("range","string").lower(), XSD.string)

        g.add((prop, RDF.type, OWL.DatatypeProperty))
        g.add((prop, RDFS.domain, dom))
        g.add((prop, RDFS.range, rng))
        if p.get("description"):
            g.add((prop, RDFS.comment, Literal(p["description"])))

    # object properties
    for p in agg.get("object_properties", []):
        prop = URIRef(str(BIZ) + p["name"])
        dom = URIRef(str(BIZ) + p["domain"])
        rng = URIRef(str(BIZ) + p["range"])

        g.add((prop, RDF.type, OWL.ObjectProperty))
        g.add((prop, RDFS.domain, dom))
        g.add((prop, RDFS.range, rng))
        if p.get("description"):
            g.add((prop, RDFS.comment, Literal(p["description"])))

    return g
