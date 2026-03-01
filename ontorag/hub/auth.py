# ontorag/hub/auth.py
"""
GitHub OAuth + JWT session tokens for the Hub API.

Env vars:
  GITHUB_CLIENT_ID      — OAuth app client ID
  GITHUB_CLIENT_SECRET  — OAuth app client secret
  HUB_JWT_SECRET        — Secret for signing session JWTs
  HUB_JWT_ALGORITHM     — Algorithm (default HS256)
  HUB_JWT_EXPIRY_HOURS  — Token lifetime in hours (default 24)
"""
from __future__ import annotations

import os
import time
from typing import Optional

import httpx
import jwt
from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ontorag.hub.models import GitHubUser
from ontorag.verbosity import get_logger

_log = get_logger("ontorag.hub.auth")

# ── Config ───────────────────────────────────────────────────────────

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
JWT_SECRET = os.getenv("HUB_JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = os.getenv("HUB_JWT_ALGORITHM", "HS256")
JWT_EXPIRY_HOURS = int(os.getenv("HUB_JWT_EXPIRY_HOURS", "24"))

_bearer = HTTPBearer(auto_error=False)


# ── GitHub OAuth exchange ────────────────────────────────────────────

async def github_exchange_code(code: str) -> str:
    """Exchange an OAuth authorization code for a GitHub access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    token = data.get("access_token")
    if not token:
        _log.info("OAuth exchange failed: %s", data)
        raise HTTPException(status_code=401, detail="GitHub OAuth exchange failed")
    return token


async def github_get_user(gh_token: str) -> GitHubUser:
    """Fetch the authenticated GitHub user profile."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    return GitHubUser(
        login=data["login"],
        id=data["id"],
        avatar_url=data.get("avatar_url", ""),
        name=data.get("name"),
        email=data.get("email"),
    )


# ── JWT helpers ──────────────────────────────────────────────────────

def create_session_token(user: GitHubUser, gh_token: str) -> str:
    """Create a signed JWT that embeds the GitHub user info and token."""
    now = int(time.time())
    payload = {
        "sub": user.login,
        "uid": user.id,
        "gh_token": gh_token,
        "iat": now,
        "exp": now + JWT_EXPIRY_HOURS * 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> dict:
    """Decode and verify a session JWT.  Raises HTTPException on failure."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


# ── FastAPI dependency ───────────────────────────────────────────────

class CurrentUser:
    """Resolved from the JWT — carries both the GitHub login and token."""
    __slots__ = ("login", "uid", "gh_token")

    def __init__(self, login: str, uid: int, gh_token: str):
        self.login = login
        self.uid = uid
        self.gh_token = gh_token

    def __repr__(self) -> str:
        return f"CurrentUser(login={self.login!r})"


async def require_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> CurrentUser:
    """FastAPI dependency — extracts and verifies the session JWT."""
    if creds is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    payload = decode_session_token(creds.credentials)
    return CurrentUser(
        login=payload["sub"],
        uid=payload["uid"],
        gh_token=payload["gh_token"],
    )
