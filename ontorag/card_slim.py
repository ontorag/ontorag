"""Per-chunk schema-card slimming.

Every LLM prompt in extraction embeds the schema card. On large baselines (e.g.
68 rpg classes plus induced terms) that makes each prompt huge and slow, even
though only a few classes are relevant to any single chunk. :func:`slim_card`
prunes the card, per chunk, to:

  - all **induced/local** terms (kept always — the domain-specific ones), plus
  - **baseline** terms lexically present in the chunk text, plus
  - properties whose (kept) domain or range survived.

Slimming is **opt-in** (global ``--slim-card`` flag or ``ONTORAG_SLIM_CARD=1``):
it cuts prompt size and token cost, but because it prunes baseline classes not
lexically present in a chunk it can lower instance-extraction recall (a baseline
class named differently from the text — e.g. ``Proficiency`` vs "abilities" —
gets dropped). It is safe and useful for ontology *induction* (align-schema
reconciles anything re-proposed) and as a cost saver when the baseline is large.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Set

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.card_slim")

_force_slim = False


def set_slim(enabled: bool) -> None:
    """CLI override (``--slim-card``) to prune the card per chunk."""
    global _force_slim
    _force_slim = bool(enabled)


def _slim_enabled() -> bool:
    if _force_slim:
        return True
    return os.getenv("ONTORAG_SLIM_CARD", "").strip().lower() in ("1", "true", "yes", "on")


def _tokens(name: str) -> List[str]:
    """Split a CamelCase / snake name into lowercase word tokens."""
    return [p.lower() for p in re.findall(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])|\d+", name)]


def _is_local(origin: str) -> bool:
    return (origin or "").strip().lower() in ("", "induced")


def _mentioned(name: str, text_l: str, words: Set[str]) -> bool:
    if not name:
        return False
    n = name.lower()
    if re.search(r"\b" + re.escape(n) + r"\b", text_l):     # whole-name match
        return True
    return any(t in words for t in _tokens(name) if len(t) >= 4)  # any significant token


def slim_card(card: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Return a copy of *card* pruned to the terms relevant to *text*.

    Induced/local terms are always kept. Returns *card* unchanged unless slimming
    is enabled (opt-in). Namespace, aliases and other keys are preserved.
    """
    if not _slim_enabled():
        return card

    text_l = (text or "").lower()
    words = set(re.findall(r"[a-z]{3,}", text_l))

    classes = card.get("classes", [])
    kept_classes = [
        c for c in classes
        if _is_local(c.get("origin", "")) or _mentioned(c.get("name", ""), text_l, words)
    ]
    kept_names = {c.get("name") for c in kept_classes}

    def _keep_prop(p: Dict[str, Any]) -> bool:
        return (
            _is_local(p.get("origin", ""))
            or p.get("domain") in kept_names
            or p.get("range") in kept_names
            or _mentioned(p.get("name", ""), text_l, words)
        )

    dt = card.get("datatype_properties", [])
    op = card.get("object_properties", [])
    kept_dt = [p for p in dt if _keep_prop(p)]
    kept_op = [p for p in op if _keep_prop(p)]

    out = dict(card)
    out["classes"] = kept_classes
    out["datatype_properties"] = kept_dt
    out["object_properties"] = kept_op
    _log.debug("slim_card: classes %d->%d, dt_props %d->%d, obj_props %d->%d",
               len(classes), len(kept_classes), len(dt), len(kept_dt), len(op), len(kept_op))
    return out
