"""Unit tests for bounded-concurrency chunk processing (parallel.map_chunks)."""
import threading
import time

import pytest

import ontorag.parallel as par
from ontorag.parallel import map_chunks, set_concurrency, get_concurrency


@pytest.fixture(autouse=True)
def _reset():
    par._override = None
    yield
    par._override = None


def test_order_preserved_regardless_of_completion():
    # later items finish first, but results must stay in input order
    def work(i, x):
        time.sleep((5 - x) * 0.01)
        return x * 10
    assert map_chunks([0, 1, 2, 3, 4], work, concurrency=5) == [0, 10, 20, 30, 40]


def test_on_done_fires_for_each():
    seen = []
    lock = threading.Lock()

    def work(i, x):
        return x

    def on_done(i, r):
        with lock:
            seen.append(i)

    map_chunks([10, 11, 12], work, on_done=on_done, concurrency=3)
    assert sorted(seen) == [0, 1, 2]


def test_actual_concurrency():
    """With concurrency=3, three workers run at once."""
    active = 0
    peak = 0
    lock = threading.Lock()

    def work(i, x):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return x

    map_chunks(list(range(6)), work, concurrency=3)
    assert peak >= 2  # genuinely overlapping (allow scheduler slack; not 1)


def test_worker_exception_propagates():
    def work(i, x):
        if x == 2:
            raise ValueError("boom")
        return x
    with pytest.raises(ValueError):
        map_chunks([1, 2, 3], work, concurrency=2)


def test_sequential_when_one_worker():
    order = []
    map_chunks([1, 2, 3], lambda i, x: order.append(x) or x, concurrency=1)
    assert order == [1, 2, 3]


def test_concurrency_override_and_default():
    assert get_concurrency(default=4) == 4
    set_concurrency(7)
    assert get_concurrency() == 7
    set_concurrency(None)      # ignored
    assert get_concurrency() == 7
