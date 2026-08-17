"""Publish a locally-built OntoRAG dataset to a GitHub repo so the Hub can
explore or fork it.

A dataset built by the CLI is just a directory of files (ontology/world.ttl,
content/chunks.jsonl, …). To be usable by the Hub it needs a ``manifest.json``
that *respects the ontorag structure* — an ``ontorag`` spec version plus an
``ontology.graph`` pointer. This module synthesizes that manifest (inferring the
base IRI and entity counts straight from the graph) when one isn't present, then
commits the dataset in a single commit via the GitHub Git Data API. The caller
chooses whether to upload the original corpus (``content/sources/``) or only the
derived ontology + graph.
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.hub")

_API = "https://api.github.com"
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}
_DTO_DIR = ("content", "dto")            # intermediate DTOs — never worth publishing
_SOURCES_DIR = ("content", "sources")    # the raw corpus (optional)


# ── auth ─────────────────────────────────────────────────────────────

def _token(explicit: Optional[str]) -> str:
    tok = explicit or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not tok:
        raise RuntimeError(
            "no GitHub token — pass --token or set GITHUB_TOKEN (a PAT with 'repo' scope)")
    return tok


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _me(token: str) -> str:
    r = requests.get(f"{_API}/user", headers=_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()["login"]


# ── manifest synthesis ───────────────────────────────────────────────

def _split_iri(uri: str) -> Tuple[str, str]:
    s = str(uri)
    i = max(s.rfind("#"), s.rfind("/"))
    return (s[:i + 1], s[i + 1:]) if i >= 0 else ("", s)


def _infer_graph_stats(graph_path: Path) -> Tuple[str, Dict[str, int], int, Dict[str, str]]:
    """(base_iri, by_type_local_counts, total_entities, prefixes) from the graph."""
    import re
    from collections import Counter
    from rdflib import Graph, RDF, URIRef

    g = Graph()
    g.parse(str(graph_path), format="turtle")

    by_type: Dict[str, int] = {}
    subjects = set()
    ns_counter: Counter = Counter()
    used_ns = set()
    for s, p, o in g:
        for term in (s, p, o):
            if isinstance(term, URIRef):
                used_ns.add(_split_iri(str(term))[0])
        if p == RDF.type and isinstance(o, URIRef):
            _, local = _split_iri(str(o))
            by_type[local] = by_type.get(local, 0) + 1
            subjects.add(s)
            ns_counter[_split_iri(str(s))[0]] += 1

    base_iri = ns_counter.most_common(1)[0][0] if ns_counter else ""
    by_type = dict(sorted(by_type.items(), key=lambda kv: -kv[1]))

    # prefixes: read the turtle @prefix header directly (faithful to the dataset's
    # own naming — rdflib rebinds/injects ~25 defaults) and keep only the ones the
    # data actually uses
    declared: Dict[str, str] = {}  # namespace -> prefix
    header = graph_path.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r'@prefix\s+([A-Za-z][\w.\-]*)\s*:\s*<([^>]+)>\s*\.', header):
        declared[m.group(2)] = m.group(1)
    prefixes = {declared[ns]: ns for ns in used_ns if ns in declared}
    prefixes = dict(sorted(prefixes.items()))
    return base_iri, by_type, len(subjects), prefixes


def _count_lines(path: Path) -> int:
    n = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def _build_manifest(d: Path, graph_rel: str, name: str, license: str,
                    base_iri_override: Optional[str]) -> Tuple[dict, Optional[str]]:
    """Return (manifest, generated_prefixes_json_or_None)."""
    graph_path = d / graph_rel
    if not graph_path.exists():
        raise RuntimeError(
            f"graph not found at '{graph_rel}' — pass --graph to point at your world/instance TTL")

    base_iri, by_type, entities, prefixes = _infer_graph_stats(graph_path)
    if base_iri_override:
        base_iri = base_iri_override

    ontology = {"format": "text/turtle", "graph": graph_rel, "base_iri": base_iri,
                "counts": {"entities": entities, "by_type": by_type}}
    for key, rel in (("schema", "ontology/schema.ttl"),
                     ("entity_index", "ontology/entities.jsonl")):
        if (d / rel).exists():
            ontology[key] = rel

    # prefixes.json: reference an existing one, else emit one from the graph bindings
    prefixes_json = None
    if (d / "ontology/prefixes.json").exists():
        ontology["prefixes"] = "ontology/prefixes.json"
    elif prefixes:
        ontology["prefixes"] = "ontology/prefixes.json"
        prefixes_json = json.dumps(prefixes, indent=2, ensure_ascii=False) + "\n"

    content: dict = {}
    counts: Dict[str, int] = {}
    if (d / "content/chunks.jsonl").exists():
        content["chunks"] = "content/chunks.jsonl"
        counts["chunks"] = _count_lines(d / "content/chunks.jsonl")
    if (d / "content/sources.json").exists():
        content["sources"] = "content/sources.json"
        try:
            srcs = json.loads((d / "content/sources.json").read_text(encoding="utf-8"))
            counts["documents"] = len(srcs)
        except Exception:
            pass
    if counts:
        content["counts"] = counts

    manifest = {"ontorag": "0.1",
                "dataset": {"name": name, "license": license},
                "ontology": ontology,
                "content": content}
    return manifest, prefixes_json


# ── file gathering ───────────────────────────────────────────────────

def _gather(d: Path, include_sources: bool) -> Dict[str, bytes]:
    files: Dict[str, bytes] = {}
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(d).parts
        if any(seg in _SKIP_DIRS for seg in parts):
            continue
        if p.suffix == ".pyc" or p.name == ".env" or p.name.startswith(".env."):
            continue
        if parts[:2] == _DTO_DIR:               # intermediate DTOs
            continue
        if not include_sources and parts[:2] == _SOURCES_DIR:   # raw corpus (keep sources.json)
            continue
        files[p.relative_to(d).as_posix()] = p.read_bytes()
    return files


# ── GitHub repo + commit ─────────────────────────────────────────────

def _default_branch(owner: str, repo: str, token: str) -> Optional[str]:
    r = requests.get(f"{_API}/repos/{owner}/{repo}", headers=_headers(token), timeout=30)
    if r.status_code == 200:
        return r.json().get("default_branch") or "main"
    return None


def _create_repo(owner: str, repo: str, token: str, private: bool,
                 description: str, me: str) -> str:
    url = f"{_API}/user/repos" if owner == me else f"{_API}/orgs/{owner}/repos"
    r = requests.post(url, headers=_headers(token), timeout=30,
                      json={"name": repo, "private": private,
                            "description": description, "auto_init": False})
    if r.status_code != 201:
        raise RuntimeError(f"could not create {owner}/{repo}: {r.status_code} {r.text}")
    return r.json().get("default_branch") or "main"


def _ensure_initialized(owner: str, repo: str, token: str, branch: str) -> None:
    """The Git Data API refuses to create blobs in a repo with no commits ("Git
    Repository is empty"). Seed an initial commit via the Contents API when the
    branch has no ref yet — covers both a freshly-created and a pre-existing
    empty repo."""
    h = _headers(token)
    if requests.get(f"{_API}/repos/{owner}/{repo}/git/ref/heads/{branch}",
                    headers=h, timeout=30).status_code == 200:
        return
    seed = base64.b64encode(b"# OntoRAG dataset\n").decode("ascii")
    p = requests.put(f"{_API}/repos/{owner}/{repo}/contents/README.md", headers=h, timeout=30,
                     json={"message": "ontorag: initialize", "content": seed, "branch": branch})
    if p.status_code not in (200, 201):
        raise RuntimeError(f"could not initialize {owner}/{repo}: {p.status_code} {p.text}")
    for _ in range(15):   # ref propagation is not instantaneous
        if requests.get(f"{_API}/repos/{owner}/{repo}/git/ref/heads/{branch}",
                        headers=h, timeout=30).status_code == 200:
            return
        time.sleep(1.0)


def _commit_all(owner: str, repo: str, token: str, branch: str,
                files: Dict[str, bytes], message: str) -> str:
    h = _headers(token)
    base = f"{_API}/repos/{owner}/{repo}/git"

    _ensure_initialized(owner, repo, token, branch)
    ref = requests.get(f"{base}/ref/heads/{branch}", headers=h, timeout=30)
    ref.raise_for_status()
    head = ref.json()["object"]["sha"]
    parents = [head]
    commit = requests.get(f"{base}/commits/{head}", headers=h, timeout=30)
    commit.raise_for_status()
    base_tree = commit.json()["tree"]["sha"]

    tree: List[dict] = []
    total = len(files)
    for i, (path, data) in enumerate(files.items(), 1):
        try:
            payload = {"content": data.decode("utf-8"), "encoding": "utf-8"}
        except UnicodeDecodeError:
            payload = {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"}
        b = requests.post(f"{base}/blobs", headers=h, json=payload, timeout=120)
        if b.status_code not in (200, 201):
            raise RuntimeError(f"blob upload failed for {path}: {b.status_code} {b.text}")
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": b.json()["sha"]})
        if total > 20 and i % 20 == 0:
            _log.info("  uploaded %d/%d blobs", i, total)

    tpayload: dict = {"tree": tree}
    if base_tree:
        tpayload["base_tree"] = base_tree
    t = requests.post(f"{base}/trees", headers=h, json=tpayload, timeout=60)
    t.raise_for_status()

    c = requests.post(f"{base}/commits", headers=h, timeout=30,
                      json={"message": message, "tree": t.json()["sha"], "parents": parents})
    c.raise_for_status()
    sha = c.json()["sha"]

    if parents:
        u = requests.patch(f"{base}/refs/heads/{branch}", headers=h,
                           json={"sha": sha, "force": False}, timeout=30)
    else:
        u = requests.post(f"{base}/refs", headers=h,
                          json={"ref": f"refs/heads/{branch}", "sha": sha}, timeout=30)
    u.raise_for_status()
    return sha


# ── entry point ──────────────────────────────────────────────────────

def push_dataset(dataset_dir: str, repo: str, token: Optional[str] = None,
                 include_sources: bool = True, private: bool = True,
                 name: Optional[str] = None, license: str = "",
                 base_iri: Optional[str] = None, graph: str = "ontology/world.ttl",
                 message: Optional[str] = None, regen_manifest: bool = False) -> dict:
    d = Path(dataset_dir).resolve()
    if not d.is_dir():
        raise RuntimeError(f"{d} is not a directory")

    tok = _token(token)
    me = _me(tok)
    owner, repo_name = repo.split("/", 1) if "/" in repo else (me, repo)

    manifest_path = d / "manifest.json"
    if manifest_path.exists() and not regen_manifest:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _log.info("using existing manifest.json")
    else:
        manifest, prefixes_json = _build_manifest(
            d, graph, name or repo_name, license, base_iri)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if prefixes_json is not None:
            (d / "ontology").mkdir(parents=True, exist_ok=True)
            (d / "ontology/prefixes.json").write_text(prefixes_json, encoding="utf-8")
            _log.info("wrote ontology/prefixes.json (from graph bindings)")
        oc = manifest["ontology"]["counts"]
        _log.info("wrote manifest.json (base_iri=%s, %d entities)",
                  manifest["ontology"]["base_iri"], oc["entities"])

    files = _gather(d, include_sources)
    if "manifest.json" not in files:
        files["manifest.json"] = manifest_path.read_bytes()
    if not files:
        raise RuntimeError("nothing to upload")

    branch = _default_branch(owner, repo_name, tok)
    created = branch is None
    if created:
        desc = (manifest.get("dataset") or {}).get("name") or repo_name
        branch = _create_repo(owner, repo_name, tok, private, f"OntoRAG dataset — {desc}", me)
        _log.info("created %s/%s (%s)", owner, repo_name, "private" if private else "public")

    msg = message or (
        f"ontorag: publish dataset ({'with' if include_sources else 'without'} corpus sources)")
    sha = _commit_all(owner, repo_name, tok, branch, files, msg)

    return {"repo": f"{owner}/{repo_name}", "branch": branch, "commit": sha,
            "created": created, "files": len(files),
            "include_sources": include_sources,
            "url": f"https://github.com/{owner}/{repo_name}"}
