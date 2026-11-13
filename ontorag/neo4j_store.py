"""Neo4j sink: map an instances TTL into a native labelled property graph.

Neo4j is an LPG, not a triple store — it does not answer SPARQL, and neosemantics
imports RDF without changing that. So Neo4j is a **parallel serving target**, not a
`SparqlBackend`: `world.ttl` stays canonical and this module projects it.

The projection is deliberately native rather than RDF-shaped (n10s puts everything
on `:Resource {uri}`), so the result is pleasant to query in Cypher:

    ns:Character/ab12cd a ns:Character   ->  (:Character:Resource {iri, label})
      rdfs:label "Bonisagus"             ->  .label
      ns:age "1080"                      ->  .age            (datatype property)
      ns:memberOf -> ns:Faction/xy       ->  -[:MEMBER_OF]->(:Faction)
      prov:wasDerivedFrom [ a mcp:Mention ;
        prov:value "…" ; mcp:chunkId … ] ->  -[:DERIVED_FROM]->(:Mention {quote, chunkId, …})

`graph_to_rows()` is pure and has no driver dependency, so the mapping is testable
offline; `write_rows()` writes it through any Cypher runner and `load_rows()` does
the Neo4j I/O. The same projection and queries (`CypherBackend`) also serve the
embedded relate store (`relate_store.py`). The `neo4j` driver is imported lazily, so the
package still imports without the `[neo4j]` extra.

Mentions are blank nodes in the source graph (`instances_to_ttl.py`), so they carry
no stable identity: re-importing would duplicate every citation. We mint a
deterministic key from (instance IRI, chunk id, quote) and MERGE on it, which makes
re-import idempotent — the same property instances already get from
`_stable_instance_iri`.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from pathlib import Path
from abc import abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from ontorag.mcp_backend import GraphBackend, results_json, term
from ontorag.verbosity import get_logger

_log = get_logger("ontorag.neo4j_store")

BATCH = 1000
PROV_VALUE = "http://www.w3.org/ns/prov#value"

# Subjects typed with one of these are schema, not data — skip them, so pointing
# this at a concatenated schema+world TTL still yields only instances.
_SCHEMA_TYPES = {
    "http://www.w3.org/2002/07/owl#Class",
    "http://www.w3.org/2002/07/owl#ObjectProperty",
    "http://www.w3.org/2002/07/owl#DatatypeProperty",
    "http://www.w3.org/2002/07/owl#AnnotationProperty",
    "http://www.w3.org/2002/07/owl#Ontology",
    "http://www.w3.org/2000/01/rdf-schema#Class",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#Property",
}
# Typed on instances but meaningless as a Neo4j label.
_NOISE_TYPES = {"http://www.w3.org/2002/07/owl#NamedIndividual"}

_ORP = "https://ontorag.org/provenance#"
_OA = "http://www.w3.org/ns/oa#"
_DCTERMS = "http://purl.org/dc/terms/"
# The provenance scaffolding around orp:Mention (https://ontorag.org/provenance/):
# where a passage lives, not facts about the world. Mentions are flattened onto
# their (:Mention) node, so these are not projected as (:Resource) nodes.
_PROVENANCE_TYPES = {_ORP + t for t in ("Source", "SourceFile", "Pack", "SpinePack", "Chunk")}


# ── naming ───────────────────────────────────────────────────────────

def local_name(iri: str) -> str:
    return re.split(r"[#/]", str(iri).rstrip("/#"))[-1] if iri else ""


def safe_label(name: str) -> str:
    """Neo4j node label from a class local name (`Character` -> `Character`)."""
    s = re.sub(r"[^0-9A-Za-z_]", "_", name)
    return ("N" + s) if (not s or s[0].isdigit()) else s


def rel_type(name: str) -> str:
    """Neo4j relationship type from a predicate local name, in house style:
    `memberOf` -> `MEMBER_OF`, `hasTag` -> `HAS_TAG`."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"[^0-9A-Za-z_]", "_", s).upper()
    return ("R" + s) if (not s or s[0].isdigit()) else s


def mention_key(instance_iri: str, chunk_id: str, quote: str) -> str:
    """Stable identity for a blank-node Mention, so re-import doesn't duplicate."""
    return hashlib.sha1(f"{instance_iri}|{chunk_id}|{quote}".encode("utf-8")).hexdigest()


def _py(value: Any) -> Any:
    """rdflib term -> a value the Neo4j driver accepts."""
    if isinstance(value, Literal):
        try:
            v = value.toPython()
        except Exception:
            return str(value)
        return v if isinstance(v, (str, int, float, bool)) else str(v)
    return str(value)


def _add_prop(props: Dict[str, Any], key: str, value: Any) -> None:
    """Repeated predicates collapse into a list property rather than last-wins."""
    if key not in props:
        props[key] = value
        return
    cur = props[key]
    if isinstance(cur, list):
        if value not in cur:
            cur.append(value)
    elif cur != value:
        props[key] = [cur, value]


# ── mapping (pure — no driver, no I/O) ───────────────────────────────

def _flatten_orp_mention(g: Graph, mention) -> Tuple[Dict[str, Any], List[str]]:
    """The payload of an orp:Mention as flat properties: the quote and page/section
    come from its target's selectors, the chunk id and source title from the nodes
    it points at. Keys match the legacy mcp:Mention projection where one existed."""
    t_has_target, t_has_source = URIRef(_OA + "hasTarget"), URIRef(_OA + "hasSource")
    t_selector, t_exact = URIRef(_OA + "hasSelector"), URIRef(_OA + "exact")
    m: Dict[str, Any] = {}
    iris: List[str] = []

    def put(key: str, value, iri: str) -> None:
        if value is not None and key not in m:
            m[key] = _py(value)
            iris.append(iri)

    for target in g.objects(mention, t_has_target):
        for sel in g.objects(target, t_selector):
            put("value", g.value(sel, t_exact), _OA + "exact")
            put("page", g.value(sel, URIRef(_ORP + "pageStart")), _ORP + "pageStart")
            put("pageLabel", g.value(sel, URIRef(_ORP + "pageLabel")), _ORP + "pageLabel")
            put("section", g.value(sel, URIRef(_ORP + "sectionTitle")), _ORP + "sectionTitle")
        file_ = g.value(target, t_has_source)
        if file_ is not None:
            source = g.value(file_, URIRef(_ORP + "fileOf"))
            if source is not None:
                put("source", g.value(source, URIRef(_DCTERMS + "title")), _DCTERMS + "title")
    chunk = g.value(mention, URIRef(_ORP + "inChunk"))
    if chunk is not None:
        put("chunkId", g.value(chunk, URIRef(_DCTERMS + "identifier")), _DCTERMS + "identifier")
    return m, iris


def graph_to_rows(g: Graph) -> Dict[str, List[dict]]:
    """Project an rdflib instances graph into Neo4j-ready rows.

    Returns {"nodes": [...], "rels": [...], "mentions": [...]}. Node rows carry the
    full class IRIs alongside the short labels, because local names collide across
    namespaces (a baseline `rpg:Character` and a local `ns:Character` both shorten
    to `Character`) and the IRI is what disambiguates them.

    Object properties whose target is not itself an instance (an external URL or a
    vocabulary term) become IRI-valued node properties rather than relationships,
    since there would be nothing to MATCH on the far end.
    """
    mention_ids: set = set()
    for s, _p, o in g.triples((None, RDF.type, None)):
        if local_name(o) == "Mention":
            mention_ids.add(s)

    nodes: Dict[str, dict] = {}
    rels: List[dict] = []
    mentions: List[dict] = []

    for subj in set(g.subjects()):
        if subj in mention_ids or isinstance(subj, BNode):
            continue
        types = [str(t) for t in g.objects(subj, RDF.type)]
        if not types or any(t in _SCHEMA_TYPES or t in _PROVENANCE_TYPES for t in types):
            continue

        iri = str(subj)
        class_iris = [t for t in types if t not in _NOISE_TYPES]
        node = {
            "iri": iri,
            "label": None,
            "classes": sorted({safe_label(local_name(t)) for t in class_iris}),
            "class_iris": sorted(class_iris),
            "props": {},
            # property keys are local names; keep the real predicate IRIs so a
            # reader can reconstruct faithful RDF instead of inventing one
            "prop_iris": [],
        }

        for p, o in g.predicate_objects(subj):
            if p == RDF.type:
                continue
            name = local_name(p)
            if o in mention_ids:                                  # provenance
                if (o, URIRef(_OA + "hasTarget"), None) in g:     # orp:Mention
                    m, m_iris = _flatten_orp_mention(g, o)
                else:                                             # legacy mcp:Mention
                    m, m_iris = {}, []
                    for mp, mo in g.predicate_objects(o):
                        if mp == RDF.type:
                            continue
                        m[local_name(mp)] = _py(mo)
                        m_iris.append(str(mp))
                quote = m.pop("value", "")                        # the quoted passage
                chunk_id = str(m.get("chunkId", ""))
                if not quote:
                    continue
                mentions.append({
                    "key": mention_key(iri, chunk_id, str(quote)),
                    "iri": iri,
                    "pred": str(p),          # prov:wasDerivedFrom — kept so the
                    # quote keeps prov:value's IRI so describe() can rebuild it
                    "props": {"quote": quote, **m, "propertyIris": sorted(set(m_iris))},
                })
            elif isinstance(o, URIRef):                           # object property
                rels.append({"from": iri, "to": str(o),
                             "type": rel_type(name), "iri": str(p)})
            elif p == RDFS.label:
                node["label"] = _py(o)
            else:                                                 # datatype property
                _add_prop(node["props"], name, _py(o))
                if str(p) not in node["prop_iris"]:
                    node["prop_iris"].append(str(p))

        nodes[iri] = node

    # An object property may point outside the graph — at a vocabulary term or a
    # plain URL (dc:license -> creativecommons.org, schema:url -> a homepage).
    # Those are real data, not dangling edges, and there is no node to MATCH at
    # load time, so keep them as IRI-valued properties on the source node.
    known = set(nodes)
    kept: List[dict] = []
    external = 0
    for r in rels:
        if r["to"] in known:
            kept.append(r)
        elif r["from"] in known:
            _add_prop(nodes[r["from"]]["props"], local_name(r["iri"]), r["to"])
            if r["iri"] not in nodes[r["from"]]["prop_iris"]:
                nodes[r["from"]]["prop_iris"].append(r["iri"])
            external += 1
    if external:
        _log.info("neo4j: %d object propert%s pointed outside the graph and became "
                  "IRI-valued node properties", external, "y" if external == 1 else "ies")

    _log.info("neo4j mapping: %d nodes, %d relationships, %d mentions",
              len(nodes), len(kept), len(mentions))
    return {"nodes": list(nodes.values()), "rels": kept, "mentions": mentions}


# ── driver I/O ───────────────────────────────────────────────────────

def _driver(uri: Optional[str], user: Optional[str], password: Optional[str]):
    try:
        from neo4j import GraphDatabase
    except ImportError as e:
        raise RuntimeError(
            "Neo4j export needs the official driver:\n"
            "    pip install 'ontorag[neo4j]'"
        ) from e
    uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = user or os.getenv("NEO4J_USER", "neo4j")
    password = password or os.getenv("NEO4J_PASSWORD", "")
    _log.info("neo4j: connecting to %s as %s", uri, user)
    return GraphDatabase.driver(uri, auth=(user, password))


def _batches(rows: List[dict], size: int = BATCH):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def write_rows(run: Callable[..., Any], rows: Dict[str, List[dict]], *,
               batch: int = BATCH) -> Dict[str, int]:
    """Write mapped rows through any Cypher runner: `run(query, **params)`.

    Store-agnostic, so Neo4j (a driver session) and relate (an embedded
    connection) load exactly the same projection with exactly the same Cypher.
    Idempotent: everything MERGEs on a stable key.
    """
    counts = {"nodes": 0, "rels": 0, "mentions": 0}
    run("CREATE CONSTRAINT ontorag_resource_iri IF NOT EXISTS "
        "FOR (n:Resource) REQUIRE n.iri IS UNIQUE")
    run("CREATE CONSTRAINT ontorag_mention_key IF NOT EXISTS "
        "FOR (m:Mention) REQUIRE m.key IS UNIQUE")

    # labels can't be parameterised, so group by label set (one query each)
    by_labels: Dict[Tuple[str, ...], List[dict]] = defaultdict(list)
    for n in rows["nodes"]:
        by_labels[tuple(n["classes"])].append(n)
    for labels, group in by_labels.items():
        extra = "".join(f":`{l}`" for l in labels)
        q = (f"UNWIND $rows AS row "
             f"MERGE (n:Resource{extra} {{iri: row.iri}}) "
             f"SET n += row.props, n.label = row.label, "
             f"n.classIris = row.class_iris, n.propertyIris = row.prop_iris")
        for chunk in _batches(group, batch):
            run(q, rows=chunk)
            counts["nodes"] += len(chunk)

    # same for relationship types
    by_type: Dict[str, List[dict]] = defaultdict(list)
    for r in rows["rels"]:
        by_type[r["type"]].append(r)
    for rtype, group in by_type.items():
        q = (f"UNWIND $rows AS row "
             f"MATCH (a:Resource {{iri: row.from}}) "
             f"MATCH (b:Resource {{iri: row.to}}) "
             f"MERGE (a)-[rel:`{rtype}`]->(b) SET rel.iri = row.iri")
        for chunk in _batches(group, batch):
            run(q, rows=chunk)
            counts["rels"] += len(chunk)

    q = ("UNWIND $rows AS row "
         "MATCH (n:Resource {iri: row.iri}) "
         "MERGE (m:Mention {key: row.key}) SET m += row.props "
         "MERGE (n)-[d:DERIVED_FROM]->(m) SET d.iri = row.pred")
    for chunk in _batches(rows["mentions"], batch):
        run(q, rows=chunk)
        counts["mentions"] += len(chunk)
    return counts


def load_rows(rows: Dict[str, List[dict]], *, uri: Optional[str] = None,
              user: Optional[str] = None, password: Optional[str] = None,
              database: Optional[str] = None, wipe: bool = False,
              batch: int = BATCH) -> Dict[str, int]:
    """Write mapped rows into Neo4j. Idempotent: everything MERGEs on a stable key."""
    database = database or os.getenv("NEO4J_DATABASE") or None

    with _driver(uri, user, password) as drv:
        with drv.session(database=database) as ses:
            if wipe:
                _log.warning("neo4j: --wipe — deleting existing :Resource / :Mention nodes")
                ses.run("MATCH (n) WHERE n:Resource OR n:Mention "
                        "CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS")
            counts = write_rows(lambda q, **p: ses.run(q, **p), rows, batch=batch)

    _log.info("neo4j: loaded %(nodes)d nodes, %(rels)d relationships, %(mentions)d mentions",
              counts)
    return counts


def neo4j_upload_ttl(ttl_path: str, *, uri: Optional[str] = None,
                     user: Optional[str] = None, password: Optional[str] = None,
                     database: Optional[str] = None, wipe: bool = False,
                     batch: int = BATCH) -> Dict[str, int]:
    """Parse an instances TTL and project it into Neo4j."""
    g = Graph()
    g.parse(str(Path(ttl_path)), format="turtle")
    _log.info("neo4j: parsed %s (%d triples)", ttl_path, len(g))
    return load_rows(graph_to_rows(g), uri=uri, user=user, password=password,
                     database=database, wipe=wipe, batch=batch)


# ── MCP backend (Cypher) ─────────────────────────────────────────────

# Structural properties written by load_rows(); not part of the data.
_RESERVED = {"iri", "label", "classIris", "propertyIris"}


MAX_PATH_HOPS = 6
CYPHER_SCHEMA = (
    "Graph shape: every instance is (:Resource:<ClassLocalName> {iri, label, "
    "classIris, propertyIris, <datatype props by local name>}); object properties "
    "are relationships typed UPPER_SNAKE (memberOf -> MEMBER_OF) carrying the "
    "predicate IRI in rel.iri; source passages are (:Resource)-[:DERIVED_FROM]->"
    "(:Mention {quote, chunkId, source, page, section}). Pass values as $params."
)


class CypherBackend(GraphBackend):
    """`GraphBackend` over the labelled-property-graph projection, in Cypher.

    The projection (`graph_to_rows` + `write_rows`) is the same whichever store
    holds it, so the queries are too: a subclass only says how to run a read
    (`_read`). `Neo4jBackend` does that over Bolt, `relate_store.RelateBackend`
    in-process.

    An LPG does not answer SPARQL, so this deliberately does **not** subclass
    `SparqlBackend` — the MCP server drops `sparql_select`/`sparql_construct` and
    offers `cypher_query` instead, plus the traversals a property graph is good
    at (`shortest_path`, `neighbourhood`). Navigation still returns SPARQL Results
    JSON, so the rest of the tool contract is unchanged.

    Subclasses must make `_read` refuse writes at the store level — a real
    guarantee rather than a regex over the query text.

    One honest divergence from the RDF backends: the projection stores datatype
    properties as node properties keyed by local name, so predicate IRIs are
    recovered from `propertyIris` where possible. Where a property has no recorded
    IRI, `outgoing()` reports the bare local name rather than inventing one.
    """

    @abstractmethod
    def _read(self, cypher: str, **params) -> List[dict]:
        """Run a read-only query; rows as plain dicts (nodes as property maps)."""

    def close(self) -> None:
        pass

    # -- traversals: what a property graph adds over the navigation primitives --

    def shortest_path(self, from_iri: str, to_iri: str,
                      max_hops: int = 4) -> Dict[str, Any]:
        """The shortest chain of relationships linking two instances, in either
        direction, one row per hop with the edge in its stored orientation."""
        hops = max(1, min(int(max_hops), MAX_PATH_HOPS))   # interpolated: keep an int
        rows = self._read(
            "MATCH (a:Resource {iri: $a}), (b:Resource {iri: $b}) "
            f"MATCH p = shortestPath((a)-[*..{hops}]-(b)) "
            "RETURN [r IN relationships(p) | startNode(r).iri] AS s, "
            "[r IN relationships(p) | coalesce(r.iri, type(r))] AS p, "
            "[r IN relationships(p) | endNode(r).iri] AS o",
            a=from_iri, b=to_iri)
        out: List[Dict[str, Any]] = []
        if rows:
            r = rows[0]
            for i, (s, p, o) in enumerate(zip(r["s"] or [], r["p"] or [], r["o"] or []), 1):
                out.append({"step": term(i), "s": term(s, "uri"),
                            "p": term(p, "uri" if _looks_iri(p) else "literal"),
                            "o": term(o, "uri")})
        return results_json(["step", "s", "p", "o"], out)

    def neighbourhood(self, iri: str, depth: int = 1, limit: int = 100,
                      fan_out: int = 25) -> Dict[str, Any]:
        """Instances within `depth` (1 or 2) hops, with the edge that reaches them.

        Depth 2 expands only `fan_out` first-hop neighbours: a hub (a setting that
        every entity is `inWorld`) has thousands of edges, and walking all of them
        is slow and says nothing.
        """
        cols = ("coalesce(b.label, b.prefLabel, b.name) AS label, "
                "b.classIris AS classes")
        rows = self._read(
            "MATCH (a:Resource {iri: $iri})-[r]-(b:Resource) "
            "RETURN 1 AS hop, null AS via, coalesce(r.iri, type(r)) AS p, "
            "startNode(r) = a AS out, b.iri AS n, " + cols + " LIMIT $limit",
            iri=iri, limit=int(limit))
        if int(depth) >= 2 and len(rows) < int(limit):
            rows += self._read(
                "MATCH (a:Resource {iri: $iri})-[]-(m:Resource) "
                "WITH DISTINCT a, m LIMIT $fan "
                "MATCH (m)-[r]-(b:Resource) WHERE b <> a "
                "RETURN 2 AS hop, m.iri AS via, coalesce(r.iri, type(r)) AS p, "
                "startNode(r) = m AS out, b.iri AS n, " + cols + " LIMIT $limit",
                iri=iri, fan=int(fan_out), limit=int(limit) - len(rows))
        return results_json(
            ["hop", "via", "p", "direction", "n", "label", "class"],
            [{"hop": term(r["hop"]), "via": term(r["via"], "uri"),
              "p": term(r["p"], "uri" if _looks_iri(r["p"]) else "literal"),
              "direction": term("out" if r["out"] else "in"),
              "n": term(r["n"], "uri"), "label": term(r["label"]),
              "class": term((r["classes"] or [None])[0], "uri")} for r in rows])

    # -- raw escape hatch, exposed as the `cypher_query` MCP tool --

    def cypher(self, query: str, limit: int = 200,
               params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run a read-only Cypher query; rows come back as SPARQL Results JSON.
        `params` are passed as real Cypher parameters — never interpolate values."""
        rows = self._read(query, **(params or {}))[:limit]
        variables: List[str] = []
        for r in rows:
            for k in r:
                if k not in variables:
                    variables.append(k)
        return results_json(variables, [
            {k: term(r.get(k), "uri" if _looks_iri(r.get(k)) else "literal")
             for k in variables} for r in rows])

    # -- GraphBackend --

    def list_by_class(self, class_iri: str, limit: int = 50) -> Dict[str, Any]:
        # Narrow by the class's node label first (label lookups are indexed), then
        # check the full IRI: local names collide across namespaces.
        label = safe_label(local_name(class_iri))      # sanitised: safe to interpolate
        rows = self._read(
            f"MATCH (n:Resource:`{label}`) WHERE $cls IN n.classIris "
            "RETURN n.iri AS s, n.label AS label LIMIT $limit",
            cls=class_iri, limit=int(limit))
        return results_json(["s", "label"], [
            {"s": term(r["s"], "uri"), "label": term(r["label"])} for r in rows])

    def outgoing(self, iri: str, limit: int = 100) -> Dict[str, Any]:
        node = self._read("MATCH (n:Resource {iri: $iri}) "
                          "RETURN properties(n) AS props, n.classIris AS classes",
                          iri=iri)
        rows: List[Dict[str, Any]] = []
        if node:
            props = node[0]["props"] or {}
            by_local = {local_name(i): i for i in (props.get("propertyIris") or [])}
            for cls in node[0]["classes"] or []:
                rows.append({"p": term(str(RDF.type), "uri"), "o": term(cls, "uri")})
            if props.get("label"):
                rows.append({"p": term(str(RDFS.label), "uri"),
                             "o": term(props["label"])})
            for key, value in props.items():
                if key in _RESERVED:
                    continue
                pred = by_local.get(key, key)          # bare local name if unrecorded
                for v in (value if isinstance(value, list) else [value]):
                    rows.append({"p": term(pred, "uri" if _looks_iri(pred) else "literal"),
                                 "o": term(v, "uri" if _looks_iri(v) else "literal")})

        for r in self._read(
                "MATCH (a:Resource {iri: $iri})-[rel]->(b) "
                "RETURN coalesce(rel.iri, type(rel)) AS p, b.iri AS o, b.key AS mkey, "
                "b.quote AS quote LIMIT $limit", iri=iri, limit=int(limit)):
            target = r["o"] or r["mkey"]               # mentions have a key, not an iri
            rows.append({"p": term(r["p"], "uri" if _looks_iri(r["p"]) else "literal"),
                         "o": term(target, "uri" if _looks_iri(target) else "literal")})
        return results_json(["p", "o"], rows[:int(limit)])

    def incoming(self, iri: str, limit: int = 100) -> Dict[str, Any]:
        rows = self._read(
            "MATCH (a:Resource)-[rel]->(b:Resource {iri: $iri}) "
            "RETURN a.iri AS s, coalesce(rel.iri, type(rel)) AS p LIMIT $limit",
            iri=iri, limit=int(limit))
        return results_json(["s", "p"], [
            {"s": term(r["s"], "uri"),
             "p": term(r["p"], "uri" if _looks_iri(r["p"]) else "literal")}
            for r in rows])

    def mentions(self, iris, limit: int = 8) -> Dict[str, Any]:
        iris = list(iris)
        cols = ["s", "quote", "source", "chunkId", "page", "section"]  # = SparqlBackend
        if not iris:
            return results_json(cols, [])
        rows = self._read(
            "MATCH (n:Resource)-[:DERIVED_FROM]->(m:Mention) WHERE n.iri IN $iris "
            "RETURN n.iri AS s, m.quote AS quote, coalesce(m.source, m.sourcePath) AS source, "
            "m.chunkId AS chunkId, coalesce(m.pageLabel, m.page) AS page, "
            "m.section AS section LIMIT $limit", iris=iris, limit=int(limit))
        return results_json(cols, [
            {"s": term(r["s"], "uri"), "quote": term(r["quote"]),
             "source": term(r["source"]), "chunkId": term(r["chunkId"]),
             "page": term(r.get("page")), "section": term(r.get("section"))}
            for r in rows])

    def describe(self, iri: str, accept: str = "text/turtle") -> str:
        """Rebuild the node's neighbourhood as RDF and serialise it, so `describe`
        returns the same media types as the RDF backends."""
        g = Graph()
        s = URIRef(iri)
        node = self._read("MATCH (n:Resource {iri: $iri}) "
                          "RETURN properties(n) AS props, n.classIris AS classes",
                          iri=iri)
        if not node:
            return g.serialize(format=_RDF_FORMATS.get(accept, "turtle"))

        props = node[0]["props"] or {}
        by_local = {local_name(i): i for i in (props.get("propertyIris") or [])}
        for cls in node[0]["classes"] or []:
            g.add((s, RDF.type, URIRef(cls)))
        if props.get("label"):
            g.add((s, RDFS.label, Literal(props["label"])))
        for key, value in props.items():
            if key in _RESERVED or key not in by_local:
                continue                               # no IRI recorded -> not valid RDF
            p = URIRef(by_local[key])
            for v in (value if isinstance(value, list) else [value]):
                g.add((s, p, URIRef(v) if _looks_iri(v) else Literal(v)))

        for r in self._read(
                "MATCH (a:Resource {iri: $iri})-[rel]->(b) "
                "RETURN coalesce(rel.iri, type(rel)) AS p, b.iri AS o, "
                "properties(b) AS bprops", iri=iri):
            if not _looks_iri(r["p"]):
                continue
            p = URIRef(r["p"])
            if r["o"]:
                g.add((s, p, URIRef(r["o"])))
            else:                                      # a Mention: inline its payload
                mn = BNode()
                g.add((s, p, mn))
                bprops = r["bprops"] or {}
                mby = {local_name(i): i for i in (bprops.get("propertyIris") or [])}
                mby.setdefault("quote", str(PROV_VALUE))   # stored under prov:value
                for k, v in bprops.items():
                    if k in ("key", "propertyIris") or k not in mby:
                        continue
                    g.add((mn, URIRef(mby[k]), Literal(v)))
        return g.serialize(format=_RDF_FORMATS.get(accept, "turtle"))


class Neo4jBackend(CypherBackend):
    """`CypherBackend` over a Neo4j server.

    Queries run in read transactions (`execute_read`), so the server itself
    rejects writes.
    """

    def __init__(self, uri: Optional[str] = None, user: Optional[str] = None,
                 password: Optional[str] = None, database: Optional[str] = None) -> None:
        self._driver = _driver(uri, user, password)
        self._database = database or os.getenv("NEO4J_DATABASE") or None
        _log.info("Neo4jBackend: database=%s", self._database or "<default>")

    def close(self) -> None:
        self._driver.close()

    def _read(self, cypher: str, **params) -> List[dict]:
        with self._driver.session(database=self._database) as ses:
            return ses.execute_read(
                lambda tx: [r.data() for r in tx.run(cypher, **params)])


_RDF_FORMATS = {
    "text/turtle": "turtle",
    "application/ld+json": "json-ld",
    "application/rdf+xml": "xml",
    "application/n-triples": "nt",
}


def _looks_iri(value: Any) -> bool:
    return isinstance(value, str) and bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", value))
