# ontorag/verbosity.py
"""
Centralized verbosity / logging setup for OntoRAG.

Levels:
  0  (default)  — silent: only final OK/error messages via typer.echo
  1  (-v)       — INFO: progress messages (which chunk, which file, counts)
  2  (-vv)      — DEBUG: detailed tracing (payloads, merge decisions, IRIs)
"""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup_logging(verbosity: int = 0) -> None:
    """Configure the ``ontorag`` logger hierarchy. Call once from the CLI."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    if verbosity <= 0:
        level = logging.WARNING
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.DEBUG

    handler = logging.StreamHandler(sys.stderr)
    fmt = "[%(levelname)s] %(name)s: %(message)s"
    if verbosity >= 2:
        fmt = "[%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
    handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger("ontorag")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``ontorag`` namespace."""
    return logging.getLogger(name)
