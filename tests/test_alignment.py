"""Unit tests for schema-alignment response normalization.

Regression guard for the crash where an LLM returned a bare JSON array
(``[{...}]``) instead of the documented ``{"alignments": [...]}`` envelope,
causing ``'list' object has no attribute 'get'`` in ``align_schema``.
"""
from ontorag.schema_alignment import _normalize_alignments


def test_envelope_passthrough():
    raw = {"alignments": [{"induced_name": "A", "action": "new"}]}
    assert _normalize_alignments(raw) == raw


def test_bare_array_is_wrapped():
    """The bug: a bare array must be wrapped, not crash downstream."""
    raw = [{"induced_name": "A", "action": "reuse"},
           {"induced_name": "B", "action": "new"}]
    out = _normalize_alignments(raw)
    assert out == {"alignments": raw}
    # downstream summary must be able to call .get() on every entry
    assert [a.get("action") for a in out["alignments"]] == ["reuse", "new"]


def test_alt_key_wrapping():
    raw = {"result": [{"induced_name": "A", "action": "extend"}]}
    assert _normalize_alignments(raw) == {"alignments": raw["result"]}


def test_non_dict_entries_dropped():
    raw = [{"induced_name": "A", "action": "new"}, ["garbage"], "nope", 3]
    assert _normalize_alignments(raw) == {"alignments": [{"induced_name": "A", "action": "new"}]}


def test_empty_and_junk():
    assert _normalize_alignments({}) == {"alignments": []}
    assert _normalize_alignments(None) == {"alignments": []}
    assert _normalize_alignments("some string") == {"alignments": []}
    assert _normalize_alignments([]) == {"alignments": []}
