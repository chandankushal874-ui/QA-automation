"""Phase 2 MFA (TOTP) + Phase 3 OIDC status (no external IdP required for unit tests)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from src.api.app import JOBS_STORE, SessionLocal, app
from src.api.auth import ensure_seed_users
from src.core.mfa import generate_mfa_secret, verify_totp
from src.models.database import (
    AuditEvent,
    AuthProfile,
    CrawlState,
    Issue,
    IssueOccurrence,
    Job,
)


@pytest.fixture
def client():
    JOBS_STORE.clear()
    db = SessionLocal()
    try:
        for model in (AuthProfile, IssueOccurrence, Issue, CrawlState, Job, AuditEvent):
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
    return TestClient(app)


def _register(client: TestClient, uname: str | None = None) -> tuple[str, str]:
    uname = uname or f"mfa_{uuid.uuid4().hex[:10]}"
    r = client.post(
        "/api/auth/register",
        json={"username": uname, "password": "pass1234"},
    )
    assert r.status_code == 201, r.text
    return uname, r.json()["token"]


def test_totp_helper_roundtrip():
    secret = generate_mfa_secret()
    import pyotp

    code = pyotp.TOTP(secret).now()
    assert verify_totp(secret, code) is True
    assert verify_totp(secret, "000000") is False


def test_mfa_enroll_and_login_step_up(client):
    uname, token = _register(client)
    headers = {"Authorization": f"Bearer {token}"}

    setup = client.post("/api/auth/mfa/setup", headers=headers)
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://")

    import pyotp

    code = pyotp.TOTP(secret).now()
    conf = client.post(
        "/api/auth/mfa/confirm",
        headers=headers,
        json={"code": code},
    )
    assert conf.status_code == 200
    assert conf.json()["mfaEnabled"] is True

    # Password login now requires MFA
    login = client.post(
        "/api/auth/login",
        json={"username": uname, "password": "pass1234"},
    )
    assert login.status_code == 200
    body = login.json()
    assert body["mfa_required"] is True
    assert body.get("token") in ("", None)
    assert body.get("mfa_token")

    code2 = pyotp.TOTP(secret).now()
    done = client.post(
        "/api/auth/mfa/verify",
        json={"mfa_token": body["mfa_token"], "code": code2},
    )
    assert done.status_code == 200
    assert done.json()["token"]
    assert done.json()["user"]["username"] == uname


def test_password_login_without_mfa_still_works(client):
    uname, _ = _register(client)
    r = client.post(
        "/api/auth/login",
        json={"username": uname, "password": "pass1234"},
    )
    assert r.status_code == 200
    assert r.json()["token"]
    assert r.json().get("mfa_required") is False


def test_oidc_status_when_disabled(client):
    r = client.get("/api/auth/oidc/status")
    assert r.status_code == 200
    body = r.json()
    assert "oidcEnabled" in body
    assert "oidcConfigured" in body
    # Default: not configured → loginUrl null
    if not body["oidcConfigured"]:
        assert body["loginUrl"] is None
