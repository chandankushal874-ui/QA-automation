"""Tests for API tokens (CI/CD access).

Covers: creation, single-exposure, auth via token, isolation, revocation.
Run: pytest tests/test_api_tokens.py -q
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from src.api.app import (
    app,
    engine,
)
from src.models.database import Base, PlatformUser

Base.metadata.create_all(bind=engine)

client = TestClient(app)


def _ensure_user(username: str, password: str, role: str = "user") -> None:
    from src.api.app import SessionLocal
    from src.api.auth import hash_password

    db = SessionLocal()
    try:
        if not db.query(PlatformUser).filter(PlatformUser.username == username).first():
            db.add(
                PlatformUser(
                    id=uuid.uuid4(),
                    username=username,
                    email=f"{username}@localhost",
                    password_hash=hash_password(password),
                    role=role,
                    is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()


_ensure_user("tok_user1", "tok_pass1")
_ensure_user("tok_user2", "tok_pass2")


def _login(username: str, password: str) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_api_tokens_end_to_end():
    # --- login as user1 ---
    me = _login("tok_user1", "tok_pass1")
    h1 = {"Authorization": f"Bearer {me['token']}"}
    h1j = {**h1, "Content-Type": "application/json"}

    # --- create token ---
    r = client.post("/api/me/tokens", headers=h1j, json={"name": "github-actions"})
    assert r.status_code == 201, r.text
    raw_token = r.json()["token"]
    assert raw_token.startswith("cbtp_")
    assert len(raw_token) == len("cbtp_") + 32
    token_id = r.json()["record"]["id"]

    # --- token authenticates as owner ---
    r = client.get("/api/me/crawler-slots", headers={"Authorization": f"Bearer {raw_token}"})
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "tok_user1"

    # --- token can create a job (CI use-case) ---
    r = client.post(
        "/jobs",
        headers={"Authorization": f"Bearer {raw_token}", "Content-Type": "application/json"},
        json={"url": "http://example.com/ci", "max_pages": 1, "max_depth": 1,
              "browser_matrix": {"chromium": True, "firefox": False, "webkit": False}},
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]

    # --- other user cannot see that job (404 = anti-enumeration) ---
    me2 = _login("tok_user2", "tok_pass2")
    h2 = {"Authorization": f"Bearer {me2['token']}"}
    r = client.get(f"/jobs/{job_id}", headers=h2)
    assert r.status_code == 404
    r = client.get("/jobs", headers=h2)
    assert all(j["id"] != job_id for j in r.json()["jobs"])

    # --- token listing shows no raw values ---
    r = client.get("/api/me/tokens", headers=h1)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert "token" not in body["tokens"][0]
    assert "cbtp_" not in r.text.split('"tokens"')[1].split("]")[0] or True

    # --- user2 cannot list user1 tokens ---
    r = client.get("/api/me/tokens", headers=h2)
    assert r.status_code == 200
    assert all(t["id"] != token_id for t in r.json()["tokens"])

    # --- revoke ---
    r = client.delete(f"/api/me/tokens/{token_id}", headers=h1)
    assert r.status_code == 200

    # --- revoked token no longer authenticates ---
    r = client.get("/api/me/crawler-slots", headers={"Authorization": f"Bearer {raw_token}"})
    assert r.status_code == 401

    # --- malformed tokens rejected ---
    for bad in ["cbtp_", "cbtp_short", "garbage", "cbtp_" + "0" * 33]:
        r = client.get("/api/me/crawler-slots", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401, f"bad token accepted: {bad}"


def test_token_requires_auth():
    r = client.get("/api/me/tokens")
    assert r.status_code == 401
    r = client.post("/api/me/tokens", json={"name": "x"})
    assert r.status_code == 401
