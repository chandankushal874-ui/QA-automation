"""OIDC Authorization Code + PKCE client (Phase 3).

Configure via env (no hardcoded IdP secrets):
  OIDC_ENABLED=true
  OIDC_ISSUER=https://accounts.google.com   (or your Okta/Azure issuer)
  OIDC_CLIENT_ID=...
  OIDC_CLIENT_SECRET=...
  OIDC_REDIRECT_URI=http://localhost:8000/api/auth/oidc/callback
  OIDC_SCOPES=openid profile email
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from src.config import settings

logger = logging.getLogger(__name__)

_discovery_cache: dict[str, Any] = {"at": 0.0, "doc": None}
_jwks_client: PyJWKClient | None = None


def oidc_configured() -> bool:
    return bool(
        getattr(settings, "oidc_enabled", False)
        and (settings.oidc_issuer or "").strip()
        and (settings.oidc_client_id or "").strip()
        and (settings.oidc_redirect_uri or "").strip()
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge S256)."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def generate_state_nonce() -> tuple[str, str]:
    return secrets.token_urlsafe(24), secrets.token_urlsafe(24)


async def fetch_discovery() -> dict[str, Any]:
    if not oidc_configured():
        raise RuntimeError("OIDC is not configured")
    now = time.time()
    if _discovery_cache["doc"] and now - float(_discovery_cache["at"]) < 3600:
        return _discovery_cache["doc"]  # type: ignore[return-value]

    issuer = settings.oidc_issuer.rstrip("/")
    url = f"{issuer}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        doc = resp.json()
    _discovery_cache["doc"] = doc
    _discovery_cache["at"] = now
    return doc


async def build_authorize_url(
    *,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    doc = await fetch_discovery()
    auth_ep = doc.get("authorization_endpoint")
    if not auth_ep:
        raise RuntimeError("OIDC discovery missing authorization_endpoint")
    scopes = (settings.oidc_scopes or "openid profile email").strip()
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{auth_ep}?{urlencode(params)}"


async def exchange_code(code: str, code_verifier: str) -> dict[str, Any]:
    doc = await fetch_discovery()
    token_ep = doc.get("token_endpoint")
    if not token_ep:
        raise RuntimeError("OIDC discovery missing token_endpoint")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "client_id": settings.oidc_client_id,
        "code_verifier": code_verifier,
    }
    if settings.oidc_client_secret:
        data["client_secret"] = settings.oidc_client_secret
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(token_ep, data=data)
        if resp.status_code >= 400:
            logger.error("OIDC token error: %s %s", resp.status_code, resp.text[:300])
            resp.raise_for_status()
        return resp.json()


def decode_id_token_unverified(id_token: str) -> dict[str, Any]:
    """Decode JWT payload without signature verify.

    INSECURE — only used when OIDC_ALLOW_UNVERIFIED_TOKENS=true (local dev).
    Production logins go through verify_id_token (JWKS signature check).
    """
    import json

    parts = id_token.split(".")
    if len(parts) < 2:
        raise ValueError("Invalid id_token")
    payload = parts[1]
    pad = 4 - len(payload) % 4
    if pad != 4:
        payload += "=" * pad
    raw = base64.urlsafe_b64decode(payload.encode("ascii"))
    return json.loads(raw)


def _get_jwks_client(jwks_uri: str) -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None or _jwks_client.uri != jwks_uri:
        _jwks_client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)
    return _jwks_client


async def verify_id_token(id_token: str, *, nonce: str) -> dict[str, Any]:
    """Verify an IdP id_token: JWKS signature + iss/aud/exp/nonce.

    Works with Google (https://accounts.google.com) and any standards-compliant
    OIDC provider whose discovery document exposes jwks_uri.
    Raises ValueError on any verification failure.
    """
    if not id_token:
        raise ValueError("Missing id_token")

    if getattr(settings, "oidc_allow_unverified_tokens", False):
        logger.warning("OIDC_ALLOW_UNVERIFIED_TOKENS=true — skipping id_token signature check (dev only)")
        claims = decode_id_token_unverified(id_token)
    else:
        doc = await fetch_discovery()
        jwks_uri = doc.get("jwks_uri")
        if not jwks_uri:
            raise ValueError("OIDC discovery missing jwks_uri")
        try:
            signing_key = _get_jwks_client(jwks_uri).get_signing_key_from_jwt(id_token)
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=settings.oidc_client_id,
                issuer=settings.oidc_issuer.rstrip("/"),
            )
        except jwt.PyJWTError as e:
            raise ValueError(f"id_token verification failed: {e}") from e

    token_nonce = str(claims.get("nonce") or "")
    if not nonce or token_nonce != nonce:
        raise ValueError("id_token nonce mismatch")
    return claims


async def fetch_userinfo(access_token: str) -> dict[str, Any]:
    doc = await fetch_discovery()
    ui = doc.get("userinfo_endpoint")
    if not ui:
        return {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(ui, headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code >= 400:
            return {}
        return resp.json()
