"""Tests for the tunnels API (CRUD + owner isolation)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from src.api.app import app
from src.models.database import Base, PlatformUser

Base.metadata.create_all(bind=__import__("src.api.app", fromlist=["engine"]).engine)

client = TestClient(app)


def _ensure_user(username: str, password: str) -> str:
    from src.api.app import SessionLocal
    from src.api.auth import hash_password

    db = SessionLocal()
    try:
        u = db.query(PlatformUser).filter_by(username=username).first()
        if not u:
            u = PlatformUser(
                id=uuid.uuid4(), username=username, email=f"{username}@localhost",
                password_hash=hash_password(password), role="user", is_active=True,
            )
            db.add(u)
            db.commit()
        return str(u.id)
    finally:
        db.close()


def _login(username: str, password: str) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


_ensure_user("tunnel_a", "pw")
_ensure_user("tunnel_b", "pw")


def test_tunnel_crud_and_isolation() -> None:
    h = {"Authorization": f"Bearer {_login('tunnel_a', 'pw')['token']}"}
    hb = {"Authorization": f"Bearer {_login('tunnel_b', 'pw')['token']}"}
    hc = {**h, "Content-Type": "application/json"}

    r = client.post("/api/tunnels", headers=hc, json={"name": "local", "target_origin": "http://localhost:3000"})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert r.json()["agent_token"].startswith("cbtp_")
    assert r.json()["tunnel_token"]

    # list as owner
    r = client.get("/api/tunnels", headers=h)
    assert any(t["id"] == tid for t in r.json()["tunnels"])

    # isolation: user B cannot see/revoke (404)
    r = client.get("/api/tunnels", headers=hb)
    assert all(t["id"] != tid for t in r.json()["tunnels"])
    r = client.delete(f"/api/tunnels/{tid}", headers=hb)
    assert r.status_code == 404

    # owner can revoke
    r = client.delete(f"/api/tunnels/{tid}", headers=h)
    assert r.status_code == 200


def test_relay_http_requires_existing_tunnel() -> None:
    r = client.get("/t/nonexistent-token/whatever")
    assert r.status_code == 404


def test_tunnels_require_auth() -> None:
    assert client.get("/api/tunnels").status_code == 401
    assert client.post("/api/tunnels", json={}).status_code == 401
