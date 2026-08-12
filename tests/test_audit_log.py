"""Phase 1: append-only audit log."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import JOBS_STORE, SessionLocal, app
from src.api.auth import ensure_seed_users
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
        db.query(AuthProfile).delete()
        db.query(IssueOccurrence).delete()
        db.query(Issue).delete()
        db.query(CrawlState).delete()
        db.query(Job).delete()
        db.query(AuditEvent).delete()
        db.commit()
        ensure_seed_users(db)
    except Exception:
        db.rollback()
    finally:
        db.close()
    return TestClient(app)


def test_login_writes_audit_event(client):
    from src.config import settings

    r = client.post(
        "/api/auth/login",
        json={
            "username": settings.admin_username or "admin",
            "password": settings.admin_password or "admin123",
        },
    )
    assert r.status_code == 200

    db = SessionLocal()
    try:
        rows = (
            db.query(AuditEvent).filter(AuditEvent.action == "auth.login").order_by(AuditEvent.created_at.desc()).all()
        )
        assert len(rows) >= 1
        assert rows[0].success is True
        assert rows[0].actor_username in ("admin", settings.admin_username)
    finally:
        db.close()


def test_failed_login_audited(client):
    r = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "definitely-wrong-pass"},
    )
    assert r.status_code == 401

    db = SessionLocal()
    try:
        row = (
            db.query(AuditEvent)
            .filter(AuditEvent.action == "auth.login", AuditEvent.success.is_(False))
            .order_by(AuditEvent.created_at.desc())
            .first()
        )
        assert row is not None
        assert row.actor_username == "admin"
    finally:
        db.close()


def test_register_and_job_create_audited(client):
    import uuid

    uname = f"audit_u_{uuid.uuid4().hex[:10]}"
    reg = client.post(
        "/api/auth/register",
        json={"username": uname, "password": "pass1234"},
    )
    assert reg.status_code == 201, reg.text
    token = reg.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    job = client.post(
        "/jobs",
        headers=headers,
        json={"url": "http://localhost:8090"},
    )
    assert job.status_code == 201
    job_id = job.json()["id"]

    db = SessionLocal()
    try:
        reg_ev = (
            db.query(AuditEvent)
            .filter(AuditEvent.action == "auth.register", AuditEvent.actor_username == uname)
            .order_by(AuditEvent.created_at.desc())
            .first()
        )
        assert reg_ev is not None
        assert reg_ev.success is True

        job_ev = (
            db.query(AuditEvent).filter(AuditEvent.action == "job.create", AuditEvent.resource_id == job_id).first()
        )
        assert job_ev is not None
        assert job_ev.actor_username == uname
    finally:
        db.close()


def test_admin_can_list_audit(client):
    from src.config import settings

    login = client.post(
        "/api/auth/login",
        json={
            "username": settings.admin_username or "admin",
            "password": settings.admin_password or "admin123",
        },
    )
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    r = client.get("/api/auth/audit?limit=10", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "events" in body
    assert "total" in body
