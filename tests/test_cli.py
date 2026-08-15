"""Offline smoke tests — no API keys, no network. Exercise the CLI surface and
the dependency-free markdown ingest path."""
import json
import subprocess

import pytest

try:
    import llama_index  # noqa: F401
    HAVE_LLAMAINDEX = True
except ImportError:
    HAVE_LLAMAINDEX = False


def ontorag(*args):
    return subprocess.run(["ontorag", *args], capture_output=True, text=True)


def test_help():
    r = ontorag("--help")
    assert r.returncode == 0
    assert "OntoRAG" in r.stdout


def test_subcommand_help():
    for cmd in ["ingest", "extract-schema", "build-schema-card", "export-schema-ttl",
                "extract-instances", "sparql-server", "mcp-server", "init-schema-card",
                "register-ontology", "align-schema"]:
        r = ontorag(cmd, "--help")
        assert r.returncode == 0, f"{cmd} --help failed: {r.stderr}"


def test_ingest_markdown_offline(tmp_path):
    doc = tmp_path / "sample.md"
    doc.write_text("# Title\n\nAlice knows Bob. Bob works at Acme in Paris.\n\n"
                   "## More\n\nAcme is a company.\n", encoding="utf-8")
    out = tmp_path / "dto"
    r = ontorag("ingest", str(doc), "--engine", "pageindex", "--out", str(out))
    assert r.returncode == 0, r.stderr
    jsonl = list(out.glob("chunks/*.jsonl"))
    assert jsonl, "no chunks JSONL produced"
    recs = [json.loads(line) for line in jsonl[0].read_text().splitlines() if line.strip()]
    assert recs, "no chunk records"
    assert all("text" in r or "content" in r for r in recs)


def test_llamaindex_engine_missing_is_friendly(tmp_path):
    """Without the extra, the default engine fails with a clear, actionable message
    (not a raw ModuleNotFoundError)."""
    if HAVE_LLAMAINDEX:
        pytest.skip("llama-index installed")
    doc = tmp_path / "s.md"
    doc.write_text("# T\n\nhello world.\n", encoding="utf-8")
    r = ontorag("ingest", str(doc), "--out", str(tmp_path / "d"))
    assert r.returncode != 0
    assert "ontorag[llamaindex]" in (r.stdout + r.stderr)
