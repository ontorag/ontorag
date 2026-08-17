"""Robust JSON extraction from LLM responses.

Regression for a real GitHub-Actions failure: on a dense chunk, the model
returned a *truncated* instances array and ``json.loads`` raised
``JSONDecodeError: Expecting value`` deep in the decoder, aborting the whole
extract run (the thread pool re-raises worker exceptions). The parser must
salvage the complete objects, and the extractor must skip an unsalvageable
chunk rather than crash.
"""
import pytest

from ontorag.jsonparse import loads_lenient, strip_fences, _salvage_array
import ontorag.instance_extractor_openrouter as ie


def test_direct_and_fenced():
    assert loads_lenient('{"a": 1}') == {"a": 1}
    assert loads_lenient('```json\n{"a": 1}\n```') == {"a": 1}
    assert strip_fences('```json\n{"x":2}\n```') == '{"x":2}'


def test_prose_wrapped_and_trailing_junk():
    assert loads_lenient('Here is the JSON:\n{"a": 1}\nHope that helps!') == {"a": 1}
    assert loads_lenient('{"a": 1}\n\nNote: derived from the text.') == {"a": 1}


def test_truncated_instances_salvaged():
    # array cut off mid-object (model hit a token limit) — keep the complete ones
    truncated = (
        '{"instances": ['
        '{"class": "Spell", "label": "Aegis of the Hearth"}, '
        '{"class": "Spell", "label": "Pilum of Fire"}, '
        '{"class": "Spell", "label": "Demon\'s Eternal Obl'   # <-- cut here
    )
    out = loads_lenient(truncated, array_key="instances")
    labels = [x["label"] for x in out["instances"]]
    assert labels == ["Aegis of the Hearth", "Pilum of Fire"]


def test_salvage_returns_none_when_nothing_complete():
    assert _salvage_array('{"instances": [ {"class": "Sp', "instances") is None


def test_unparseable_raises_valueerror():
    with pytest.raises(ValueError):
        loads_lenient("not json at all, sorry")
    # truncation without an array_key can't be salvaged → raises (caller retries/skips)
    with pytest.raises(ValueError):
        loads_lenient('{"proposed_additions": {"classes": [ {"name": "Fo')


class _FakeResp:
    def __init__(self, content):
        self._content = content
    def raise_for_status(self):
        pass
    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_extract_instances_skips_unparseable_chunk(monkeypatch):
    """One chunk whose model output never parses is skipped (not fatal); the
    other chunk still yields its proposal."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")

    def fake_post(url, headers=None, json=None, timeout=None):
        text = json["messages"][1]["content"]
        if "GOOD" in text:
            return _FakeResp('{"instances": [{"class": "Magus", "label": "Bob"}]}')
        return _FakeResp("this is not json")  # unparseable on every retry

    monkeypatch.setattr(ie.requests, "post", fake_post)
    monkeypatch.setattr(ie, "build_instance_prompt", lambda ch, card: ch["text"])

    chunks = [{"chunk_id": "c1", "text": "GOOD chunk"},
              {"chunk_id": "c2", "text": "BAD chunk"}]
    out = ie.extract_instance_chunk_proposals(chunks, {"classes": []}, max_retries=1, concurrency=1)
    assert len(out) == 1
    assert out[0]["instances"][0]["label"] == "Bob"


def test_extract_instances_salvages_truncated_chunk(monkeypatch):
    """A truncated array is salvaged end-to-end through the extractor."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    truncated = '{"instances": [{"class": "Spell", "label": "Foo"}, {"class": "Spell", "lab'
    monkeypatch.setattr(ie.requests, "post", lambda *a, **k: _FakeResp(truncated))
    monkeypatch.setattr(ie, "build_instance_prompt", lambda ch, card: ch["text"])
    out = ie.extract_instance_chunk_proposals(
        [{"chunk_id": "c1", "text": "x"}], {"classes": []}, max_retries=1, concurrency=1)
    assert len(out) == 1
    assert out[0]["instances"][0]["label"] == "Foo"
