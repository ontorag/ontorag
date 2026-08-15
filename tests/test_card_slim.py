"""Unit tests for per-chunk schema-card slimming (opt-in via --slim-card)."""
import pytest

import ontorag.card_slim as cs
from ontorag.card_slim import slim_card, set_slim


CARD = {
    "namespace": "http://x/",
    "classes": [
        {"name": "Character", "origin": "rpg"},      # baseline
        {"name": "Organization", "origin": "rpg"},   # baseline
        {"name": "Magus", "origin": "induced"},      # local
    ],
    "datatype_properties": [
        {"name": "hasName", "domain": "Character", "range": "string", "origin": "rpg"},
    ],
    "object_properties": [
        {"name": "castsSpell", "domain": "Magus", "range": "Spell", "origin": "induced"},
    ],
    "aliases": [{"names": ["a", "b"]}],
}


@pytest.fixture(autouse=True)
def _reset_slim():
    cs._force_slim = False
    yield
    cs._force_slim = False


def test_default_is_noop():
    """Without opt-in, the card passes through unchanged."""
    assert slim_card(CARD, "the magus prepares his laboratory") is CARD


def test_induced_always_kept_baseline_pruned_when_absent():
    set_slim(True)
    out = slim_card(CARD, "the magus prepares his laboratory")
    names = {c["name"] for c in out["classes"]}
    assert "Magus" in names                    # induced kept
    assert "Character" not in names            # baseline, not mentioned → pruned
    assert "Organization" not in names
    assert [p["name"] for p in out["object_properties"]] == ["castsSpell"]
    assert out["datatype_properties"] == []
    assert out["namespace"] == "http://x/"     # preserved
    assert out["aliases"]                       # preserved


def test_baseline_kept_when_mentioned():
    set_slim(True)
    out = slim_card(CARD, "the character joins an organization and gains a name")
    names = {c["name"] for c in out["classes"]}
    assert {"Character", "Organization", "Magus"} <= names
    assert [p["name"] for p in out["datatype_properties"]] == ["hasName"]


def test_env_enables_slimming(monkeypatch):
    monkeypatch.setenv("ONTORAG_SLIM_CARD", "1")
    out = slim_card(CARD, "nothing relevant here")
    assert {c["name"] for c in out["classes"]} == {"Magus"}   # only induced survives
