"""Tests for FastAPI — simple username/password auth + isolation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import JOBS_STORE, SessionLocal, app
from src.api.auth import ensure_seed_users
from src.models.database import (
    AuthProfile,
    CrawlState,
    Issue,
    IssueOccurrence,
    Job,
)


def _auth_headers(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture
def client():
    JOBS_STORE.clear()
    db = SessionLocal()
    try:
        db.query(AuthProfile).delete()
        db.query(IssueOccurrence).delete()
        db.query(Issue).delete()
        db.query(CrawlState).delete()
        db.query(Job).delete()
        # Keep platform_users but re-seed admin
        db.commit()
        ensure_seed_users(db)
    except Exception:
        db.rollback()
    finally:
        db.close()
    return TestClient(app)


@pytest.fixture
def auth(client):
    from src.config import settings

    return _auth_headers(
        client,
        settings.admin_username or "admin",
        settings.admin_password or "admin123",
    )


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_login_requires_username_and_password(client):
    # Missing username
    r = client.post("/api/auth/login", json={"password": "x"})
    assert r.status_code == 422
    # Wrong password
    r = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-password-xyz"},
    )
    assert r.status_code == 401


def test_register_unique_username(client):
    import uuid

    uname = f"alice_{uuid.uuid4().hex[:10]}"
    r1 = client.post(
        "/api/auth/register",
        json={"username": uname, "password": "pass1234"},
    )
    assert r1.status_code == 201, r1.text
    data = r1.json()
    assert data["token"]
    assert data["user"]["username"] == uname
    assert data["user"]["role"] == "user"

    # Duplicate username rejected
    r2 = client.post(
        "/api/auth/register",
        json={"username": uname, "password": "otherpass"},
    )
    assert r2.status_code == 400
    assert "taken" in r2.json()["detail"].lower()


def test_register_then_login(client):
    import uuid

    uname = f"bob_{uuid.uuid4().hex[:10]}"
    client.post(
        "/api/auth/register",
        json={"username": uname, "password": "secret99"},
    )
    r = client.post(
        "/api/auth/login",
        json={"username": uname, "password": "secret99"},
    )
    assert r.status_code == 200
    assert r.json()["user"]["username"] == uname


def test_create_job_requires_auth(client):
    response = client.post("/jobs", json={"url": "http://localhost:8090"})
    assert response.status_code == 401


def test_create_job_validation(client, auth):
    response = client.post("/jobs", json={}, headers=auth)
    assert response.status_code == 422
    response = client.post("/jobs", json={"url": "ftp://example.com"}, headers=auth)
    assert response.status_code == 422


def test_create_job_localhost(client, auth):
    response = client.post(
        "/jobs",
        headers=auth,
        json={
            "url": "http://localhost:8090",
            "scope": "same-domain",
            "browser_matrix": {"chromium": True, "firefox": False, "webkit": False},
        },
    )
    assert response.status_code == 201
    job_id = response.json()["id"]
    get_resp = client.get(f"/jobs/{job_id}", headers=auth)
    assert get_resp.status_code == 200


def test_list_jobs(client, auth):
    client.post("/jobs", headers=auth, json={"url": "http://localhost:8090"})
    client.post("/jobs", headers=auth, json={"url": "https://example.com"})
    list_resp = client.get("/jobs", headers=auth)
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 2


def test_user_a_cannot_access_user_b_job(client):
    """Unique users → isolated crawls, history, reports."""
    import uuid

    ua = f"isola_a_{uuid.uuid4().hex[:8]}"
    ub = f"isola_b_{uuid.uuid4().hex[:8]}"
    ra = client.post(
        "/api/auth/register",
        json={"username": ua, "password": "pass-a1"},
    )
    rb = client.post(
        "/api/auth/register",
        json={"username": ub, "password": "pass-b1"},
    )
    assert ra.status_code == 201, ra.text
    assert rb.status_code == 201, rb.text
    headers_a = {"Authorization": f"Bearer {ra.json()['token']}"}
    headers_b = {"Authorization": f"Bearer {rb.json()['token']}"}

    create_b = client.post(
        "/jobs",
        headers=headers_b,
        json={"url": "http://localhost:8090"},
    )
    assert create_b.status_code == 201
    job_b = create_b.json()["id"]

    assert client.get(f"/jobs/{job_b}", headers=headers_a).status_code == 404
    assert client.get(f"/jobs/{job_b}/report", headers=headers_a).status_code == 404
    assert client.post(f"/jobs/{job_b}/stop", headers=headers_a).status_code == 404
    assert client.get(f"/jobs/{job_b}", headers=headers_b).status_code == 200

    ids_a = {j["id"] for j in client.get("/jobs", headers=headers_a).json()["jobs"]}
    ids_b = {j["id"] for j in client.get("/jobs", headers=headers_b).json()["jobs"]}
    assert job_b not in ids_a
    assert job_b in ids_b


def test_admin_lists_all_usernames(client, auth):
    import uuid

    uname = f"listed_{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/auth/register",
        json={"username": uname, "password": "pass9999"},
    )
    r = client.get("/api/auth/users", headers=auth)
    assert r.status_code == 200
    names = {u["username"] for u in r.json()["users"]}
    assert "admin" in names
    assert uname in names


def test_metrics_endpoint(client, auth):
    client.post("/jobs", headers=auth, json={"url": "http://localhost:8090"})
    response = client.get("/metrics")
    assert response.status_code == 200
