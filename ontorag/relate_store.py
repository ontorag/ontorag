"""The graph as a query form: an instances TTL -> one embedded relate file.

[relate](https://relatedb.ai) is an in-process property-graph engine that speaks
Cypher and keeps a whole store in a single `.rl` file. Here it plays the part
`vector_index` plays for vectors: **a derived, disposable cache** next to the
dataset (`.ontorag/graph.rl`, gitignored), built from the committed source of
truth (`ontology/world.ttl`) and stamped so an unchanged graph is not rebuilt.

Why bother, when `mcp-server --onto --inst` already serves the TTL? Measured on
a 121k-triple dataset (13k nodes, 21.6k edges):

    startup          rdflib re-parses the TTL: 5.9 s     relate reopens: 0.34 s
    outgoing         2.6 ms                              0.36 ms
    2-hop aggregate  1,458 ms                            0.30 ms
    shortest path    impractical in SPARQL               1.7 ms

and graph search (paths, neighbourhoods, arbitrary Cypher) becomes something an
MCP client can just ask for.

What it is *not* for: vectors. relate's vector index is exact brute force over
uncompressed floats -- on 17,942 x 768 chunk vectors it built a 395 MB file and
answered top-8 in ~150 ms, against `vector_index`'s 9 MB shippable index.

Do not commit the `.rl`: it is binary (~3x the TTL) and every rebuild would add
a full new copy to the dataset's history. Rebuilding takes seconds.

The projection is the Neo4j one (`neo4j_store.graph_to_rows` + `write_rows`), and
so are the queries (`CypherBackend`); this module only knows how to hold a file.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
import atexit
import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from ontorag.neo4j_store import BATCH, CypherBackend, graph_to_rows, write_rows
from ontorag.verbosity import get_logger

_log = get_logger("ontorag.relate_store")

DEFAULT_REL = ".ontorag/graph.rl"
DEFAULT_GRAPH = "ontology/world.ttl"
# Bump when graph_to_rows()/write_rows() change what lands in the store, so
# every existing cache is rebuilt rather than served stale.
PROJECTION_VERSION = "1"
META_LABEL = "OntoragBuild"


class RelateStoreError(RuntimeError):
    """A user-facing failure: the message says what to do."""


def _relate():
    try:
        import relate
    except ImportError as e:
        raise RelateStoreError(
            "The embedded graph store needs relate:\n"
            "    pip install 'ontorag[relate]'"
        ) from e
    return relate


def relate_version() -> Optional[str]:
    try:
        return getattr(_relate(), "__version__", "unknown")
    except RelateStoreError:
        return None


# ── stamping ─────────────────────────────────────────────────────────

def stamp(sources: Iterable[Path]) -> str:
    """Fingerprint of the inputs: name + size + mtime of each TTL, the projection
    version and the engine version (a store written by one relate release is not
    promised to open in the next). Not a content hash -- re-reading megabytes of
    Turtle to decide whether to re-read them would defeat the point."""
    h = hashlib.sha256(f"proj={PROJECTION_VERSION};relate={relate_version()}".encode())
    for s in sources:
        st = Path(s).stat()
        h.update(f"\0{Path(s).name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:32]


def read_meta(path: Path) -> Optional[dict]:
    """The build record inside a store, or None if absent/unreadable."""
    if not Path(path).is_file():
        return None
    relate = _relate()
    try:
        conn = relate.connect(str(path), read_only=True)
        try:
            rows = [r.data() for r in conn.execute(
                f"MATCH (m:{META_LABEL}) RETURN properties(m) AS m LIMIT 1")]
        finally:
            conn.close()
    except relate.Error as e:
        _log.info("relate: %s unreadable (%s); will rebuild", path, e)
        return None
    return rows[0]["m"] if rows else None


# ── build ────────────────────────────────────────────────────────────

@dataclass
class BuildResult:
    path: Path
    nodes: int
    rels: int
    mentions: int
    seconds: float
    bytes: int
    skipped: bool = False


def _gitignore(out: Path) -> None:
    # The dataset is a git repo and this file is a cache, so keep it out of the
    # working tree's way rather than relying on the dataset to know about us.
    if out.parent.name == ".ontorag":
        gi = out.parent / ".gitignore"
        if not gi.exists():
            gi.write_text("# ontorag: derived indexes, never commit\n*\n", encoding="utf-8")


def _remove(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink(missing_ok=True)


def build(sources: List[str | Path], out: str | Path, *, force: bool = False,
          batch: int = 2 * BATCH) -> BuildResult:
    """Project instance TTL(s) into a relate file. Skips the work when the stamp
    in the existing store matches the inputs; writes atomically otherwise, so a
    server holding the old file never sees a half-built one."""
    from rdflib import Graph

    relate = _relate()
    sources = [Path(s) for s in sources]
    for s in sources:
        if not s.is_file():
            raise RelateStoreError(f"no such graph: {s}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _gitignore(out)

    want = stamp(sources)
    if not force:
        meta = read_meta(out)
        if meta and meta.get("stamp") == want:
            return BuildResult(out, int(meta.get("nodes", 0)), int(meta.get("rels", 0)),
                               int(meta.get("mentions", 0)), 0.0, out.stat().st_size,
                               skipped=True)

    t0 = time.time()
    g = Graph()
    for s in sources:
        g.parse(str(s), format="turtle")
    _log.info("relate: parsed %d triples from %d file(s)", len(g), len(sources))
    rows = graph_to_rows(g)

    # relate picks the layout from the suffix: only `*.rl` is a single file
    # (anything else becomes a WAL + snapshot directory), so the temp keeps it.
    if out.suffix != ".rl":
        raise RelateStoreError(f"{out}: a relate store file must end in .rl")
    tmp = out.with_name(out.stem + ".building.rl")
    _remove(tmp)
    conn = relate.connect(str(tmp))
    try:
        counts = write_rows(lambda q, **p: conn.execute(q, p).fetchall(), rows, batch=batch)
        conn.execute(
            f"CREATE (:{META_LABEL} {{stamp: $stamp, projection: $proj, relate: $rv, "
            "built_at: $at, sources: $src, nodes: $n, rels: $r, mentions: $m})",
            {"stamp": want, "proj": PROJECTION_VERSION, "rv": relate_version(),
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "src": [str(s) for s in sources], "n": counts["nodes"],
             "r": counts["rels"], "m": counts["mentions"]}).fetchall()
    except Exception:
        conn.close()
        _remove(tmp)
        raise
    conn.close()
    os.replace(tmp, out)
    res = BuildResult(out, counts["nodes"], counts["rels"], counts["mentions"],
                      time.time() - t0, out.stat().st_size)
    _log.info("relate: built %s (%d nodes, %d rels, %d mentions) in %.1fs",
              out, res.nodes, res.rels, res.mentions, res.seconds)
    return res


def resolve(target: str | Path, *, graph: str = DEFAULT_GRAPH,
            refresh: bool = True) -> Path:
    """`target` is a `.rl` file (served as is) or a dataset directory, whose
    cache is built or refreshed first when `refresh` is set."""
    target = Path(target)
    if target.is_dir():
        out = target / DEFAULT_REL
        if refresh:
            build([target / graph], out)
        elif not out.is_file():
            raise RelateStoreError(f"{out} not built — run `ontorag index-graph {target}`")
        return out
    if not target.is_file():
        raise RelateStoreError(f"{target} is neither a dataset directory nor a .rl store")
    return target


# ── serving ──────────────────────────────────────────────────────────

class RelateBackend(CypherBackend):
    """`CypherBackend` over an embedded relate store, opened **read-only**: the
    engine itself refuses writes, which is what makes `cypher_query` safe to
    expose.

    relate connections are thread-affine (DB-API threadsafety 1) and MCP tools run
    on worker threads, so one dedicated thread owns the connection and every read
    is handed to it. Queries are sub-millisecond; serialising them costs nothing.

    The connection must also be *closed* on that thread, before the interpreter
    finalises -- left to `__del__` at exit, relate's native code panics. Hence a
    hand-rolled daemon worker plus an `atexit` hook, rather than a
    `ThreadPoolExecutor`, which refuses new work once shutdown has begun.
    """

    def __init__(self, path: str | Path) -> None:
        relate = _relate()
        self.path = Path(path)
        self._jobs: "queue.Queue" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="relate", daemon=True)
        self._thread.start()
        try:
            self._conn = self._call(relate.connect, str(self.path), read_only=True)
        except Exception:
            self._stop()
            raise
        atexit.register(self.close)
        _log.info("RelateBackend: %s (read-only)", self.path)

    def _loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            fn, args, kwargs, fut = job
            if fut.set_running_or_notify_cancel():
                try:
                    fut.set_result(fn(*args, **kwargs))
                except BaseException as e:      # handed back to the caller
                    fut.set_exception(e)

    def _call(self, fn, *args, **kwargs):
        fut: Future = Future()
        self._jobs.put((fn, args, kwargs, fut))
        return fut.result()

    def _stop(self) -> None:
        self._jobs.put(None)
        self._thread.join()

    def _run(self, cypher: str, params: dict) -> List[dict]:
        return [r.data() for r in self._conn.execute(cypher, params)]

    def _read(self, cypher: str, **params) -> List[dict]:
        return self._call(self._run, cypher, params)

    def close(self) -> None:
        if not self._thread.is_alive():
            return
        try:
            if not self._conn.closed:
                self._call(self._conn.close)
        finally:
            self._stop()
            atexit.unregister(self.close)

    def meta(self) -> Optional[dict]:
        rows = self._read(f"MATCH (m:{META_LABEL}) RETURN properties(m) AS m LIMIT 1")
        return rows[0]["m"] if rows else None
