"""Phase 3: real OIDC login against a mocked Google-style IdP.

No network: discovery / token / JWKS responses are mocked; id_tokens are
RS256-signed with an in-test RSA keypair, so the JWKS signature verification
in src.core.oidc.verify_id_token is exercised for real.
"""

from __future__ import annotations

import time
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from src.api.app import JOBS_STORE, SessionLocal, app
from src.api.auth import ensure_seed_users
from src.config import settings
from src.core import oidc as oidc_mod
from src.models.database import (
    AuditEvent,
    AuthProfile,
    CrawlState,
    Issue,
    IssueOccurrence,
    Job,
    OidcState,
)

ISSUER = "https://accounts.google.com"
CLIENT_ID = "test-google-client-id.apps.googleusercontent.com"
REDIRECT_URI = "http://localhost:8000/api/auth/oidc/callback"

# In-test RSA keypair standing in for Google's JWKS
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_WRONG_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _make_id_token(*, nonce: str, sub: str, email: str, key=None) -> str:
    now = int(time.time())
    return pyjwt.encode(
        {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": sub,
            "email": email,
            "nonce": nonce,
            "iat": now,
            "exp": now + 600,
        },
        key or _PRIVATE_KEY,
        algorithm="RS256",
    )


class _FakeJwksClient:
    """Stands in for jwt.PyJWKClient without fetching Google's JWKS."""

    uri = "https://www.googleapis.com/oauth2/v3/certs"

    def get_signing_key_from_jwt(self, token: str):
        class _K:
            key = _PRIVATE_KEY.public_key()

        return _K()


@pytest.fixture
def oidc_client(monkeypatch):
    """TestClient with OIDC enabled; returns (client, patch_id_token_fn)."""
    JOBS_STORE.clear()
    db = SessionLocal()
    try:
        for model in (AuthProfile, IssueOccurrence, Issue, CrawlState, Job, AuditEvent, OidcState):
            try:
                db.query(model).delete()
            except Exception:
                db.rollback()
        db.commit()
        ensure_seed_users(db)
    except Exception:
        db.rollback()
    finally:
        db.close()

    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "oidc_client_secret", "fake-secret")
    monkeypatch.setattr(settings, "oidc_redirect_uri", REDIRECT_URI)
    monkeypatch.setattr(settings, "oidc_ui_success_url", "")
    monkeypatch.setattr(settings, "oidc_allow_unverified_tokens", False)
    monkeypatch.setattr(oidc_mod, "_discovery_cache", {"at": 0.0, "doc": None})
    monkeypatch.setattr(oidc_mod, "_jwks_client", None)
    monkeypatch.setattr(oidc_mod, "_get_jwks_client", lambda uri: _FakeJwksClient())

    state = {"id_token": ""}
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/o/oauth2/v2/auth",
                    "token_endpoint": f"{ISSUER}/token",
                    "userinfo_endpoint": f"{ISSUER}/userinfo",
                    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
                },
            )
        if url == f"{ISSUER}/token":
            return httpx.Response(
                200,
                json={
                    "id_token": state["id_token"],
                    "access_token": "ya29.fake-access-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        if url == f"{ISSUER}/userinfo":
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": f"unmocked {url}"})

    transport = httpx.MockTransport(handler)

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    # Patch before /oidc/login so discovery is also mocked (no real network).
    monkeypatch.setattr(oidc_mod.httpx, "AsyncClient", fake_async_client)

    def use_id_token(token: str) -> None:
        state["id_token"] = token

    client = TestClient(app)
    return client, use_id_token


def _start_login(client: TestClient) -> tuple[str, str]:
    """Hit /oidc/login, return (state, nonce) from the DB row + redirect URL."""
    r = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 302, r.text
    location = r.headers["location"]
    qs = parse_qs(urlparse(location).query)
    assert qs["client_id"] == [CLIENT_ID]
    assert qs["redirect_uri"] == [REDIRECT_URI]
    assert qs["code_challenge_method"] == ["S256"]
    state = qs["state"][0]

    db = SessionLocal()
    try:
        row = db.query(OidcState).filter(OidcState.state == state).first()
        assert row is not None
        nonce = row.nonce
    finally:
        db.close()
    return state, nonce


def test_oidc_status_when_configured(oidc_client):
    client, _ = oidc_client
    r = client.get("/api/auth/oidc/status")
    assert r.status_code == 200
    body = r.json()
    assert body["oidcEnabled"] is True
    assert body["oidcConfigured"] is True
    assert body["loginUrl"] == "/api/auth/oidc/login"


def test_oidc_full_login_flow_creates_user(oidc_client):
    client, use_id_token = oidc_client
    state, nonce = _start_login(client)

    sub = f"google-sub-{uuid.uuid4().hex[:12]}"
    email = f"oidc_{uuid.uuid4().hex[:10]}@gmail.com"
    use_id_token(_make_id_token(nonce=nonce, sub=sub, email=email))

    r = client.get(f"/api/auth/oidc/callback?code=fake-code&state={state}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"]
    assert body["user"]["authProvider"] == "oidc"
    assert body["user"]["email"] == email

    # Token works against /me
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["authProvider"] == "oidc"

    # Second login with same sub reuses the same account
    state2, nonce2 = _start_login(client)
    use_id_token(_make_id_token(nonce=nonce2, sub=sub, email=email))
    r2 = client.get(f"/api/auth/oidc/callback?code=fake-code&state={state2}")
    assert r2.status_code == 200
    assert r2.json()["user"]["id"] == body["user"]["id"]


def test_oidc_tampered_signature_rejected(oidc_client):
    client, use_id_token = oidc_client
    state, nonce = _start_login(client)
    # Sign with the WRONG key — JWKS verification must fail
    use_id_token(
        _make_id_token(
            nonce=nonce,
            sub=f"sub-{uuid.uuid4().hex[:8]}",
            email=f"evil_{uuid.uuid4().hex[:8]}@gmail.com",
            key=_WRONG_KEY,
        )
    )
    r = client.get(f"/api/auth/oidc/callback?code=fake-code&state={state}")
    assert r.status_code == 400
    assert "verification failed" in r.json()["detail"]


def test_oidc_wrong_nonce_rejected(oidc_client):
    client, use_id_token = oidc_client
    state, _nonce = _start_login(client)
    use_id_token(
        _make_id_token(
            nonce="attacker-controlled-nonce",
            sub=f"sub-{uuid.uuid4().hex[:8]}",
            email=f"evil_{uuid.uuid4().hex[:8]}@gmail.com",
        )
    )
    r = client.get(f"/api/auth/oidc/callback?code=fake-code&state={state}")
    assert r.status_code == 400


def test_oidc_unknown_state_rejected(oidc_client):
    client, _ = oidc_client
    r = client.get("/api/auth/oidc/callback?code=x&state=no-such-state")
    assert r.status_code == 400
    assert "Invalid OIDC state" in r.json()["detail"]


def test_oidc_login_disabled_returns_503():
    # Default settings: OIDC disabled
    JOBS_STORE.clear()
    c = TestClient(app)
    if not settings.oidc_enabled:
        r = c.get("/api/auth/oidc/login", follow_redirects=False)
        assert r.status_code == 503
