"""Regression: models sometimes emit null-valued list fields (e.g.
`"classes": null`, `"instances": null`). These must NOT crash — a crash inside
the extractor's success path was being caught and retried as if the LLM call had
failed, burning 3× the calls and stalling the run.
"""
import ontorag.ontology_extractor_openrouter as oe
import ontorag.instance_extractor_openrouter as ie
from ontorag.instances_to_ttl import instance_proposals_to_graph


def test_extract_schema_tolerates_null_lists(monkeypatch):
    monkeypatch.setattr(oe, "_chat_json", lambda s, u: {
        "proposed_additions": {"classes": None, "datatype_properties": None, "object_properties": None},
        "warnings": None,
    })
    out = oe.extract_schema_chunk_proposals(
        [{"chunk_id": "c1", "text": "hi"}], {"classes": []}, concurrency=1)
    assert len(out) == 1  # returned on first try, no crash/retry


def test_extract_instances_tolerates_null(monkeypatch):
    monkeypatch.setattr(ie, "_chat_json", lambda s, u: {"instances": None})
    out = ie.extract_instance_chunk_proposals(
        [{"chunk_id": "c1", "text": "hi"}], {"classes": []}, concurrency=1)
    assert len(out) == 1


def test_instances_to_ttl_tolerates_null_fields():
    proposals = [
        {"chunk_id": "c1", "instances": None},                       # null instances
        {"chunk_id": "c1", "instances": [
            {"class": "Magus", "label": "Bob", "attributes": None,
             "relations": None, "mentions": None},
        ]},
    ]
    g = instance_proposals_to_graph({"c1": {"provenance": {}}}, proposals, namespace="http://x/")
    assert len(g) >= 1  # at least Bob's rdf:type triple, no crash
