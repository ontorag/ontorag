"""Baseline-IRI export: aligned terms reference their origin ontology IRI."""
from rdflib import URIRef
from rdflib.namespace import RDFS

from ontorag.proposal_to_ttl import proposal_to_ttl

# normalized-proposal shape (what normalize_alignment produces from align output)
AGG = {
    "classes": [
        {"name": "Magus", "origin": "rpg", "subclass_of": "Character"},   # local, extends baseline
        {"name": "Covenant", "origin": "induced"},                         # local, new
    ],
    "datatype_properties": [],
    "object_properties": [
        {"name": "livesIn", "domain": "Magus", "range": "Covenant", "origin": "induced"},
        {"name": "hasLeader", "domain": "Covenant", "range": "Actor", "origin": "induced"},
    ],
}
PREFIXES = {"rpg": "http://rpg.example/ns#"}
AMOL = "http://amol/"


def test_extend_points_at_baseline_iri():
    g = proposal_to_ttl(AGG, biz_ns=AMOL, prefixes=PREFIXES, local_prefix="amol")
    assert (URIRef(AMOL + "Magus"), RDFS.subClassOf, URIRef("http://rpg.example/ns#Character")) in g
    # local new class stays local
    assert (URIRef(AMOL + "Covenant"), RDFS.subClassOf, None) not in g


def test_domain_range_resolve_local_and_baseline():
    g = proposal_to_ttl(AGG, biz_ns=AMOL, prefixes=PREFIXES, local_prefix="amol")
    # 'Covenant' is local; 'Magus' is local
    assert (URIRef(AMOL + "livesIn"), RDFS.domain, URIRef(AMOL + "Magus")) in g
    assert (URIRef(AMOL + "livesIn"), RDFS.range, URIRef(AMOL + "Covenant")) in g


def test_baseline_card_seeds_domain_range():
    """A baseline class referenced only as a range (Actor) resolves via the card."""
    base = {"classes": [{"name": "Actor", "origin": "rpg"}], "object_properties": [], "datatype_properties": []}
    g = proposal_to_ttl(AGG, biz_ns=AMOL, prefixes=PREFIXES, local_prefix="amol", baseline_card=base)
    assert (URIRef(AMOL + "hasLeader"), RDFS.range, URIRef("http://rpg.example/ns#Actor")) in g


def test_backward_compatible_without_prefixes():
    """No catalog/prefixes → everything local, subClassOf stays in biz_ns (old behaviour)."""
    g = proposal_to_ttl(AGG, biz_ns=AMOL, local_prefix="amol")
    assert (URIRef(AMOL + "Magus"), RDFS.subClassOf, URIRef(AMOL + "Character")) in g
