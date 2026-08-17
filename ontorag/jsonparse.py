"""Tolerant JSON extraction for LLM responses.

Models occasionally wrap the JSON in prose, append trailing commentary, or
*truncate* a long array when they hit a token limit (a dense chunk can produce
hundreds of instances). A bare ``json.loads`` then raises deep in the decoder and
— because per-chunk work is re-raised by the thread pool — aborts the whole run.

``loads_lenient`` recovers a usable object from all three cases:
  1. direct parse (the common case),
  2. the first balanced ``{...}``/``[...]`` embedded in surrounding prose,
  3. best-effort salvage of the leading complete objects from a truncated
     ``"<array_key>": [ {...}, {...}, <cut> ]`` payload.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


def strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 2:
            s = parts[1].strip()
        if s.lower().startswith("json"):
            s = s[4:].strip()
    return s


def _first_balanced(s: str) -> Optional[str]:
    """The first top-level ``{...}`` or ``[...]`` substring, respecting strings.
    Returns None when the opener is never balanced (i.e. the text is truncated)."""
    start = None
    opener = closer = ""
    for i, ch in enumerate(s):
        if ch in "{[":
            start, opener, closer = i, ch, ("}" if ch == "{" else "]")
            break
    if start is None:
        return None
    depth = 0
    in_str = esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def _salvage_array(s: str, key: str) -> Optional[Dict[str, Any]]:
    """Recover the leading complete objects from a truncated ``"key": [ ... ]``.
    Drops the final cut-off element. Returns ``{key: [objs]}`` or None."""
    k = s.find(f'"{key}"')
    lb = s.find("[", k) if k != -1 else -1
    if lb == -1:
        return None
    dec = json.JSONDecoder()
    i, n, items = lb + 1, len(s), []
    while i < n:
        while i < n and s[i] in " \t\r\n,":
            i += 1
        if i >= n or s[i] == "]":
            break
        try:
            obj, end = dec.raw_decode(s, i)
        except json.JSONDecodeError:
            break  # partial trailing element — stop here
        items.append(obj)
        i = end
    if not items:
        return None
    return {key: items}


def loads_lenient(content: str, array_key: Optional[str] = None) -> Dict[str, Any]:
    """Parse an LLM JSON response tolerantly. ``array_key``, when given, enables
    truncation salvage for a top-level ``{"<array_key>": [...]}`` shape."""
    text = strip_fences(content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    balanced = _first_balanced(text)
    if balanced:
        try:
            return json.loads(balanced)
        except json.JSONDecodeError:
            pass
    if array_key:
        salvaged = _salvage_array(text, array_key)
        if salvaged is not None:
            return salvaged
    raise ValueError(
        f"model returned unparseable JSON ({len(text)} chars); "
        f"starts with: {text[:200]!r}")
