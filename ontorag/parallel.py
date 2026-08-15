"""Bounded-concurrency chunk processing for the LLM extractors.

The per-chunk LLM call is latency-bound (a couple of minutes each on some
hosted models), so a long document processed sequentially is dominated by
wall-clock wait. Running several chunk calls at once cuts that ~N×. Order is
preserved in the returned results; a small worker pool bounds the number of
in-flight requests so we stay within provider rate limits (each worker still
retries with backoff on 429/5xx inside its own callback).
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, List, Optional

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.parallel")

_override: Optional[int] = None


def set_concurrency(n: Optional[int]) -> None:
    """CLI override (``--concurrency``). ``None`` / non-positive is ignored."""
    global _override
    if n is not None and n > 0:
        _override = n


def get_concurrency(default: int = 4) -> int:
    if _override:
        return _override
    env = os.getenv("ONTORAG_CONCURRENCY", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    return default


def map_chunks(
    items: List[Any],
    work: Callable[[int, Any], Any],
    on_done: Optional[Callable[[int, Any], None]] = None,
    concurrency: Optional[int] = None,
) -> List[Any]:
    """Run ``work(index, item)`` over *items* with bounded concurrency.

    Returns results in **input order**. ``on_done(index, result)`` fires as each
    item completes (completion order). If a worker raises, that exception
    propagates (matching the previous sequential behaviour) after in-flight work
    settles.
    """
    n = len(items)
    if n == 0:
        return []
    workers = max(1, min(concurrency or get_concurrency(), n))

    results: List[Any] = [None] * n

    if workers == 1:
        for i, it in enumerate(items):
            results[i] = work(i, it)
            if on_done:
                on_done(i, results[i])
        return [r for r in results if r is not None]

    _log.info("Processing %d chunks with concurrency=%d", n, workers)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(work, i, it): i for i, it in enumerate(items)}
        for fut in as_completed(futures):
            i = futures[fut]
            results[i] = fut.result()  # re-raises worker exceptions
            if on_done:
                on_done(i, results[i])
    return [r for r in results if r is not None]
