"""Regression: models sometimes emit null-valued list fields (e.g.
`"classes": null`, `"instances": null`). These must NOT crash — a crash inside
the extractor's success path was being caught and retried as if the LLM call had
failed, burning 3× the calls and stalling the run.
"""
import pytest

import ontorag.ontology_extractor_openrouter as oe
import ontorag.instance_extractor_openrouter as ie
from ontorag.instances_to_ttl import instance_proposals_to_graph


class _FakeResp:
    def __init__(self, content):
        self._content = content
    def raise_for_status(self):
        pass
    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_chat_json_null_content_raises_cleanly(monkeypatch):
    """A 200 response with null message.content must raise a clear error (which
    the retry loop handles) — not TypeError: object of type 'NoneType' has no len()."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setattr(ie.requests, "post", lambda *a, **k: _FakeResp(None))
    with pytest.raises(RuntimeError):
        ie._chat_json("sys", "user")


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
