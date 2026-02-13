from __future__ import annotations
import hashlib
from typing import Dict, Any, List, Optional

from rdflib import Graph, Namespace, URIRef, BNode, Literal
from rdflib.namespace import RDF, RDFS, XSD

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.instances_to_ttl")

PROV = Namespace("http://www.w3.org/ns/prov#")

def _slug(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isalnum() or ch in ("_","-")).strip("_-")

def _stable_instance_iri(ns: str, class_name: str, label: str, chunk_id: str) -> str:
    base = f"{class_name}|{label}|{chunk_id}"
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]
    return f"{ns}{class_name}/{h}"

def instance_proposals_to_graph(
    chunk_dtos_by_id: Dict[str, Dict[str, Any]],
    proposals: List[Dict[str, Any]],
    namespace: str,
) -> Graph:
    _log.info("Converting %d proposals to RDF (namespace=%s)", len(proposals), namespace)

    BIZ = Namespace(namespace)
    MCP = Namespace(namespace + "mcp/")
    g = Graph()
    g.bind("biz", BIZ)
    g.bind("prov", PROV)
    g.bind("rdfs", RDFS)
    g.bind("mcp", MCP)

    instance_count = 0
    for cp in proposals:
        chunk_id = cp.get("chunk_id", "")
        chunk = chunk_dtos_by_id.get(chunk_id, {})
        prov = (chunk.get("provenance") or {})

        for inst in cp.get("instances", []):
            cls_name = inst.get("class", "").strip()
            if not cls_name:
                continue

            label = (inst.get("label") or inst.get("id_hint") or "").strip()
            iri = _stable_instance_iri(namespace, cls_name, label, chunk_id)
            s = URIRef(iri)

            g.add((s, RDF.type, URIRef(f"{namespace}{cls_name}")))
            if label:
                g.add((s, RDFS.label, Literal(label)))
            instance_count += 1
            _log.debug("  instance: %s (%s) label=%r", cls_name, iri, label)

            # attributes (datatype properties): stored as literals
            attrs = inst.get("attributes", {}) or {}
            for prop_name, value in attrs.items():
                prop_name = (prop_name or "").strip()
                if not prop_name or value is None or value == "":
                    continue
                p = URIRef(f"{namespace}{prop_name}")
                g.add((s, p, Literal(str(value))))

            # relations (object properties): create target nodes (lightweight) if needed
            for rel in inst.get("relations", []) or []:
                pred = (rel.get("predicate") or "").strip()
                tgt_cls = (rel.get("target_class") or "").strip()
                if not pred or not tgt_cls:
                    continue

                tgt_label = (rel.get("target_label") or rel.get("target_id_hint") or "").strip()
                tgt_iri = _stable_instance_iri(namespace, tgt_cls, tgt_label, chunk_id)
                t = URIRef(tgt_iri)

                g.add((t, RDF.type, URIRef(f"{namespace}{tgt_cls}")))
                if tgt_label:
                    g.add((t, RDFS.label, Literal(tgt_label)))

                g.add((s, URIRef(f"{namespace}{pred}"), t))

            # provenance / mention nodes
            for m in inst.get("mentions", []) or []:
                quote = (m.get("quote") or "").strip()
                if not quote:
                    continue

                mn = BNode()
                g.add((mn, RDF.type, MCP.Mention))
                g.add((mn, PROV.value, Literal(quote)))
                g.add((mn, MCP.chunkId, Literal(chunk_id)))

                # best-effort provenance fields
                if prov.get("source_path"):
                    g.add((mn, MCP.sourcePath, Literal(prov["source_path"])))
                if prov.get("page") is not None:
                    g.add((mn, MCP.page, Literal(int(prov["page"]), datatype=XSD.integer)))
                if prov.get("page_label"):
                    g.add((mn, MCP.pageLabel, Literal(str(prov["page_label"]))))
                if prov.get("section"):
                    g.add((mn, MCP.section, Literal(str(prov["section"]))))

                # link instance -> mention
                g.add((s, PROV.wasDerivedFrom, mn))

    _log.info("Instance graph built: %d instances, %d triples", instance_count, len(g))
    return g
