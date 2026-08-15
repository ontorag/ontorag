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
                "register-ontology", "align-schema", "doctor"]:
        r = ontorag(cmd, "--help")
        assert r.returncode == 0, f"{cmd} --help failed: {r.stderr}"


def test_ingest_markdown_default_engine_offline(tmp_path):
    """The DEFAULT engine (builtin) ingests markdown with no deps and no API key."""
    doc = tmp_path / "sample.md"
    doc.write_text("# Title\n\nAlice knows Bob. Bob works at Acme in Paris.\n\n"
                   "## More\n\nAcme is a company.\n", encoding="utf-8")
    out = tmp_path / "dto"
    r = ontorag("ingest", str(doc), "--out", str(out))          # no --engine
    assert r.returncode == 0, r.stderr
    jsonl = list(out.glob("chunks/*.jsonl"))
    assert jsonl, "no chunks JSONL produced"
    recs = [json.loads(line) for line in jsonl[0].read_text().splitlines() if line.strip()]
    assert recs, "no chunk records"
    assert all("text" in r or "content" in r for r in recs)


def test_doctor():
    r = ontorag("doctor")
    assert r.returncode == 0
    assert "OntoRAG environment" in r.stdout
    for eng in ("builtin", "pageindex", "llamaindex", "docling", "unstructured"):
        assert eng in r.stdout, f"doctor did not list engine {eng}"


def test_llm_flags_in_help():
    """OpenRouter settings are exposed as global flags before the subcommand."""
    r = ontorag("--help")
    assert r.returncode == 0
    for flag in ("--model", "--api-key", "--base-url"):
        assert flag in r.stdout, f"global LLM flag {flag} missing from --help"


def test_model_override_reflected_in_doctor():
    """`--model` (before the subcommand) overrides OPENROUTER_MODEL, offline."""
    r = ontorag("--model", "zzz/override-model", "doctor")
    assert r.returncode == 0
    assert "model=zzz/override-model" in r.stdout


def test_hub_command_removed():
    """The hub is no longer part of the CLI surface."""
    r = ontorag("hub")
    assert r.returncode != 0
    assert "hub" in (r.stdout + r.stderr).lower()  # 'No such command' mentions it


def test_unknown_engine_rejected(tmp_path):
    doc = tmp_path / "s.md"
    doc.write_text("# T\n\nhi\n", encoding="utf-8")
    r = ontorag("ingest", str(doc), "--engine", "nope", "--out", str(tmp_path / "d"))
    assert r.returncode != 0
    assert "unknown engine" in (r.stdout + r.stderr).lower()


def test_docling_engine_missing_is_friendly(tmp_path):
    try:
        import docling  # noqa: F401
        pytest.skip("docling installed")
    except ImportError:
        pass
    doc = tmp_path / "s.md"
    doc.write_text("# T\n\nhi\n", encoding="utf-8")
    r = ontorag("ingest", str(doc), "--engine", "docling", "--out", str(tmp_path / "d"))
    assert r.returncode != 0
    assert "ontorag[docling]" in (r.stdout + r.stderr)


def test_llamaindex_engine_missing_is_friendly(tmp_path):
    """Selecting the llamaindex engine without its extra gives a clear, actionable
    message (not a raw ModuleNotFoundError)."""
    if HAVE_LLAMAINDEX:
        pytest.skip("llama-index installed")
    doc = tmp_path / "s.md"
    doc.write_text("# T\n\nhello world.\n", encoding="utf-8")
    r = ontorag("ingest", str(doc), "--engine", "llamaindex", "--out", str(tmp_path / "d"))
    assert r.returncode != 0
    assert "ontorag[llamaindex]" in (r.stdout + r.stderr)
