# ontorag/hub/github_storage.py
"""
GitHub-backed storage for user pipeline artifacts.

Every user gets a private repo ``ontorag-data`` in their own account.
Files are read/written via the GitHub Contents API so that OntoRAG Hub
never stores user data on its own servers.

Layout inside the repo:
  data/dto/documents/<document_id>.json
  data/dto/chunks/<document_id>.jsonl
  data/proposals/<document_id>.schema.json
  data/instances/<document_id>.instances.ttl
"""
from __future__ import annotations

import base64
from typing import Optional

import httpx

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.hub.github_storage")

REPO_NAME = "ontorag-data"
REPO_DESCRIPTION = "OntoRAG Hub — private pipeline artifacts (auto-managed)"

_API = "https://api.github.com"


def _headers(gh_token: str) -> dict:
    return {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


# ── Repo lifecycle ───────────────────────────────────────────────────

async def ensure_repo(gh_token: str, owner: str) -> str:
    """Create the user's ``ontorag-data`` repo if it doesn't exist.

    Returns the full repo name (``owner/ontorag-data``).
    """
    full = f"{owner}/{REPO_NAME}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{_API}/repos/{full}", headers=_headers(gh_token))
        if resp.status_code == 200:
            _log.debug("Repo %s already exists", full)
            return full

        _log.info("Creating repo %s", full)
        resp = await client.post(
            f"{_API}/user/repos",
            headers=_headers(gh_token),
            json={
                "name": REPO_NAME,
                "description": REPO_DESCRIPTION,
                "private": True,
                "auto_init": True,
            },
        )
        resp.raise_for_status()
    return full


# ── File I/O ─────────────────────────────────────────────────────────

async def file_exists(gh_token: str, repo: str, path: str) -> bool:
    """Check whether a file exists in the repo (HEAD request)."""
    async with httpx.AsyncClient() as client:
        resp = await client.head(
            f"{_API}/repos/{repo}/contents/{path}",
            headers=_headers(gh_token),
        )
    return resp.status_code == 200


async def read_file(gh_token: str, repo: str, path: str) -> Optional[str]:
    """Read a UTF-8 file from the repo.  Returns None if not found."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_API}/repos/{repo}/contents/{path}",
            headers=_headers(gh_token),
        )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    content_b64 = data.get("content", "")
    return base64.b64decode(content_b64).decode("utf-8")


async def write_file(
    gh_token: str,
    repo: str,
    path: str,
    content: str,
    message: str = "ontorag-hub: update artifact",
) -> str:
    """Create or update a file in the repo.  Returns the commit SHA."""
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")

    # Get the current SHA if the file exists (required for updates)
    sha: Optional[str] = None
    async with httpx.AsyncClient() as client:
        head = await client.get(
            f"{_API}/repos/{repo}/contents/{path}",
            headers=_headers(gh_token),
        )
        if head.status_code == 200:
            sha = head.json().get("sha")

        body: dict = {
            "message": message,
            "content": encoded,
        }
        if sha:
            body["sha"] = sha

        resp = await client.put(
            f"{_API}/repos/{repo}/contents/{path}",
            headers=_headers(gh_token),
            json=body,
        )
        resp.raise_for_status()
        commit_sha = resp.json().get("commit", {}).get("sha", "")

    _log.debug("Wrote %s/%s (commit=%s)", repo, path, commit_sha[:8])
    return commit_sha


async def write_file_bytes(
    gh_token: str,
    repo: str,
    path: str,
    data: bytes,
    message: str = "ontorag-hub: update artifact",
) -> str:
    """Create or update a binary file in the repo.  Returns the commit SHA."""
    encoded = base64.b64encode(data).decode("ascii")

    sha: Optional[str] = None
    async with httpx.AsyncClient() as client:
        head = await client.get(
            f"{_API}/repos/{repo}/contents/{path}",
            headers=_headers(gh_token),
        )
        if head.status_code == 200:
            sha = head.json().get("sha")

        body: dict = {"message": message, "content": encoded}
        if sha:
            body["sha"] = sha

        resp = await client.put(
            f"{_API}/repos/{repo}/contents/{path}",
            headers=_headers(gh_token),
            json=body,
        )
        resp.raise_for_status()
        commit_sha = resp.json().get("commit", {}).get("sha", "")

    _log.debug("Wrote bytes %s/%s (commit=%s)", repo, path, commit_sha[:8])
    return commit_sha
