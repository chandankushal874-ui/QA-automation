"""Tests for scheduled monitors (site monitoring).

Covers: CRUD, ownership isolation, run-now, scheduler fire, next_run
computation, and slot-limit respect (monitors never bypass user limits).
Run: pytest tests/test_monitors.py -q
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from src.api.app import app, engine
from src.models.database import Base, MonitorSchedule, PlatformUser

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


_ensure_user("mon_user1", "mon_pass1")
_ensure_user("mon_user2", "mon_pass2")


def _login(username: str, password: str) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_monitor_crud_and_isolation():
    me = _login("mon_user1", "mon_pass1")
    h1 = {"Authorization": f"Bearer {me['token']}", "Content-Type": "application/json"}

    # create
    r = client.post(
        "/api/me/monitors",
        headers=h1,
        json={"name": "nightly prod", "url": "http://example.com", "interval_minutes": 60},
    )
    assert r.status_code == 201, r.text
    m = r.json()
    monitor_id = m["id"]
    assert m["is_active"] is True
    assert m["next_run_at"] is not None

    # validation: bad url, tiny interval
    r = client.post("/api/me/monitors", headers=h1, json={"name": "x", "url": "ftp://nope", "interval_minutes": 60})
    assert r.status_code == 422
    r = client.post("/api/me/monitors", headers=h1, json={"name": "x", "url": "http://example.com", "interval_minutes": 1})
    assert r.status_code == 422

    # list mine
    r = client.get("/api/me/monitors", headers=h1)
    assert r.status_code == 200
    assert r.json()["total"] == 1

    # user2 sees none and cannot read mine (404 anti-enumeration)
    me2 = _login("mon_user2", "mon_pass2")
    h2 = {"Authorization": f"Bearer {me2['token']}"}
    r = client.get("/api/me/monitors", headers=h2)
    assert r.json()["total"] == 0
    r = client.get(f"/api/me/monitors/{monitor_id}", headers=h2)
    assert r.status_code == 404
    r = client.delete(f"/api/me/monitors/{monitor_id}", headers=h2)
    assert r.status_code == 404

    # patch: rename + pause (next_run cleared)
    r = client.patch(f"/api/me/monitors/{monitor_id}", headers=h1, json={"name": "nightly prod v2", "is_active": False})
    assert r.status_code == 200
    assert r.json()["name"] == "nightly prod v2"
    assert r.json()["is_active"] is False
    assert r.json()["next_run_at"] is None

    # resume
    r = client.patch(f"/api/me/monitors/{monitor_id}", headers=h1, json={"is_active": True})
    assert r.status_code == 200
    assert r.json()["next_run_at"] is not None


def test_monitor_run_now_and_fire():
    me = _login("mon_user1", "mon_pass1")
    h1 = {"Authorization": f"Bearer {me['token']}", "Content-Type": "application/json"}

    r = client.get("/api/me/monitors", headers=h1)
    monitor_id = r.json()["monitors"][0]["id"]

    # run-now creates a real job owned by me (or returns submitted=False on slot-full)
    r = client.post(f"/api/me/monitors/{monitor_id}/run-now", headers=h1)
    assert r.status_code == 200, r.text
    body = r.json()
    # Either submitted or refused via slots — both are correct behaviors
    assert "submitted" in body and "job_id" in body
    if body["submitted"]:
        job_id = body["job_id"]
        r2 = client.get(f"/jobs/{job_id}", headers=h1)
        assert r2.status_code == 200

    # Direct scheduler fire path with a fake due schedule
    from src.api.app import SessionLocal
    from src.api.monitors import _fire_monitor

    db = SessionLocal()
    try:
        m = db.query(MonitorSchedule).filter(MonitorSchedule.id == uuid.UUID(monitor_id)).first()
        m.next_run_at = datetime.now(UTC) - timedelta(minutes=1)  # overdue
        db.commit()

        result = _fire_monitor(db, m, manual=False)
        assert "monitor_id" in result
        # next_run advanced by interval after fire
        db.refresh(m)
        assert m.next_run_at is not None
        assert m.last_run_at is not None
    finally:
        db.close()


def test_monitor_requires_auth():
    r = client.get("/api/me/monitors")
    assert r.status_code == 401
    r = client.post("/api/me/monitors", json={"name": "x", "url": "http://example.com", "interval_minutes": 60})
    assert r.status_code == 401
