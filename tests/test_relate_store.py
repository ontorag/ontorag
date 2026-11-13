"""The embedded relate graph store: build, cache, and serve over MCP.

Offline and real — relate runs in-process, so unlike Neo4j there is no fake
driver: these tests write an actual `.rl` file and query it. Skipped when the
`[relate]` extra is not installed.
"""
import os
import time

import pytest

relate = pytest.importorskip("relate")

from ontorag import relate_store as rs
from ontorag.instances_to_ttl import instance_proposals_to_graph
from ontorag.mcp_backend import GraphBackend, SparqlBackend

NS = "http://amol/"
CHUNKS = {"c1": {"chunk_id": "c1", "provenance": {
    "source_path": "amol://core.pdf", "page": 12, "section": "Magi"}}}
PROPOSALS = [{
    "chunk_id": "c1",
    "instances": [
        {"class": "Magus", "label": "Bonisagus",
         "attributes": {"age": "1080"},
         "relations": [{"predicate": "memberOf", "target_class": "Faction",
                        "target_label": "Order of Hermes"}],
         "mentions": [{"quote": "Bonisagus founded the Order."}]},
        {"class": "Covenant", "label": "Durenmar",
         "relations": [{"predicate": "seatOf", "target_class": "Faction",
                        "target_label": "Order of Hermes"}]},
    ],
}]


def _values(res, var):
    return [b[var]["value"] for b in res["results"]["bindings"] if var in b]


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    d = tmp_path_factory.mktemp("ds")
    (d / "ontology").mkdir()
    instance_proposals_to_graph(CHUNKS, PROPOSALS, namespace=NS).serialize(
        destination=str(d / rs.DEFAULT_GRAPH), format="turtle")
    return d


@pytest.fixture(scope="module")
def backend(dataset):
    b = rs.RelateBackend(rs.resolve(dataset))
    yield b
    b.close()


def _iri(backend, cls):
    return _values(backend.list_by_class(NS + cls), "s")[0]


# ── the cache ────────────────────────────────────────────────────────

def test_build_writes_one_gitignored_file_and_skips_when_unchanged(dataset, backend):
    out = dataset / rs.DEFAULT_REL
    assert out.is_file()                                   # a single file, not a dir
    assert (out.parent / ".gitignore").read_text().strip().endswith("*")
    assert not list(out.parent.glob("*.building*"))        # temp swapped into place
    res = rs.build([dataset / rs.DEFAULT_GRAPH], out)
    assert res.skipped and res.nodes == 3 and res.mentions == 1


def test_changed_ttl_triggers_a_rebuild(tmp_path):
    src = tmp_path / "w.ttl"
    src.write_text("<http://x/a> a <http://x/T> .\n", encoding="utf-8")
    out = tmp_path / "g.rl"
    assert rs.build([src], out).nodes == 1
    src.write_text("<http://x/a> a <http://x/T> .\n<http://x/b> a <http://x/T> .\n",
                   encoding="utf-8")
    later = time.time() + 5
    os.utime(src, (later, later))                          # mtime is in the stamp
    res = rs.build([src], out)
    assert not res.skipped and res.nodes == 2


def test_store_must_be_a_dot_rl_file(tmp_path):
    """relate picks the layout from the suffix; anything else is a directory."""
    src = tmp_path / "w.ttl"
    src.write_text("<http://x/a> a <http://x/T> .\n", encoding="utf-8")
    with pytest.raises(rs.RelateStoreError, match=r"\.rl"):
        rs.build([src], tmp_path / "graph.db")


def test_resolve_explains_a_missing_target(tmp_path):
    with pytest.raises(rs.RelateStoreError):
        rs.resolve(tmp_path / "nope.rl")
    with pytest.raises(rs.RelateStoreError, match="index-graph"):
        rs.resolve(tmp_path, refresh=False)


# ── serving ──────────────────────────────────────────────────────────

def test_relate_is_a_cypher_graph_backend_not_sparql(backend):
    assert isinstance(backend, GraphBackend)
    assert not isinstance(backend, SparqlBackend)


def test_navigation(backend):
    magus = _iri(backend, "Magus")
    assert _values(backend.list_by_class(NS + "Magus"), "label") == ["Bonisagus"]
    preds = _values(backend.outgoing(magus), "p")
    assert NS + "age" in preds and NS + "memberOf" in preds
    assert magus in _values(backend.incoming(_iri(backend, "Faction")), "s")
    assert "Bonisagus" in backend.describe(magus)


def test_mentions_match_the_sparql_contract(backend):
    res = backend.mentions([_iri(backend, "Magus")])
    assert res["head"]["vars"] == ["s", "quote", "source", "chunkId", "page", "section"]
    assert _values(res, "quote") == ["Bonisagus founded the Order."]
    assert _values(res, "chunkId") == ["c1"]


def test_shortest_path_walks_edges_in_either_direction(backend):
    magus, covenant = _iri(backend, "Magus"), _iri(backend, "Covenant")
    res = backend.shortest_path(magus, covenant)
    # Magus -memberOf-> Faction <-seatOf- Covenant: each hop keeps its orientation
    assert _values(res, "step") == ["1", "2"]
    assert _values(res, "p") == [NS + "memberOf", NS + "seatOf"]
    assert _values(res, "s") == [magus, covenant]
    assert backend.shortest_path(magus, covenant, max_hops=1)["results"]["bindings"] == []


def test_neighbourhood_reports_hop_direction_and_via(backend):
    magus, covenant = _iri(backend, "Magus"), _iri(backend, "Covenant")
    faction = _iri(backend, "Faction")
    one = backend.neighbourhood(magus)
    assert _values(one, "n") == [faction] and _values(one, "direction") == ["out"]
    two = backend.neighbourhood(magus, depth=2)
    rows = {b["n"]["value"]: b for b in two["results"]["bindings"]}
    assert rows[covenant]["hop"]["value"] == "2"
    assert rows[covenant]["via"]["value"] == faction
    assert rows[covenant]["direction"]["value"] == "in"   # Covenant -seatOf-> Faction


def test_store_refuses_writes(backend):
    """Opened read-only: the engine refuses, so cypher_query is safe to expose."""
    with pytest.raises(relate.Error):
        backend.cypher("CREATE (:Evil {x: 1})")
    assert backend.cypher("MATCH (n:Evil) RETURN n")["results"]["bindings"] == []


def test_reads_from_any_thread(backend):
    """relate connections are thread-affine; MCP tools run on worker threads."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as ex:
        out = list(ex.map(lambda _: backend.list_by_class(NS + "Magus"), range(8)))
    assert all(_values(r, "label") == ["Bonisagus"] for r in out)


@pytest.mark.asyncio
async def test_mcp_exposes_cypher_and_graph_search(backend):
    from ontorag.mcp_server import create_mcp_app
    tools = {t.name: t for t in await create_mcp_app(backend).list_tools()}
    assert {"describe", "list_by_class", "outgoing", "incoming", "mentions",
            "cypher_query", "shortest_path", "neighbourhood"} <= set(tools)
    assert not {"sparql_select", "sparql_construct"} & set(tools)
    assert "DERIVED_FROM" in tools["cypher_query"].description  # schema is advertised


def test_unclosed_backend_exits_cleanly(dataset):
    """Left to `__del__` at interpreter exit, relate's native close panics; the
    backend closes on its own thread from an atexit hook instead."""
    import subprocess
    import sys
    code = ("from ontorag.relate_store import RelateBackend, resolve; "
            f"b = RelateBackend(resolve({str(dataset)!r}, refresh=False)); "
            "b.list_by_class('http://amol/Magus')")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=120)
    assert p.returncode == 0, p.stderr
    assert "panicked" not in p.stderr and "Exception ignored" not in p.stderr
