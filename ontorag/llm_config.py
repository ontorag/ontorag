"""Central resolution of OpenRouter/LLM settings.

Precedence: **explicit CLI override > environment variable > built-in default**.

The root Typer callback (``ontorag --model … --api-key … <command>``) calls
:func:`set_overrides`. The ``*_openrouter`` modules and ``schema_alignment`` read
through the getters here *at call time*, so an override registered after those
modules were imported is still honoured (the values are not frozen at import).
"""
from __future__ import annotations

import os
from typing import Optional

_DEFAULTS = {
    "OPENROUTER_MODEL": "openai/gpt-4o-mini",
    "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
    "OPENROUTER_APP_NAME": "OntoRAG",
    "OPENROUTER_SITE_URL": "https://ontorag.github.io",
    # OPENROUTER_API_KEY has no default — it must come from env or --api-key.
}

_overrides: dict[str, str] = {}


def set_overrides(
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    app_name: Optional[str] = None,
    site_url: Optional[str] = None,
) -> None:
    """Register CLI-provided values. ``None`` means "not set" and is ignored,
    so environment variables and defaults keep applying for those fields."""
    mapping = {
        "OPENROUTER_API_KEY": api_key,
        "OPENROUTER_MODEL": model,
        "OPENROUTER_BASE_URL": base_url,
        "OPENROUTER_APP_NAME": app_name,
        "OPENROUTER_SITE_URL": site_url,
    }
    for key, value in mapping.items():
        if value is not None:
            _overrides[key] = value


def _get(key: str) -> Optional[str]:
    if key in _overrides:
        return _overrides[key]
    return os.getenv(key, _DEFAULTS.get(key))


def api_key() -> Optional[str]:
    return _get("OPENROUTER_API_KEY")


def model() -> str:
    return _get("OPENROUTER_MODEL")


def base_url() -> str:
    return _get("OPENROUTER_BASE_URL")


def app_name() -> str:
    return _get("OPENROUTER_APP_NAME")


def site_url() -> str:
    return _get("OPENROUTER_SITE_URL")
