"""Tests for the journeys API (CRUD + owner isolation).

Run: pytest tests/test_journeys_api.py -q
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from src.api.app import app
from src.models.database import Base, Job, JobStatus, PlatformUser

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


def _make_job(owner_id: str, url: str) -> str:
    from src.api.app import SessionLocal

    db = SessionLocal()
    try:
        job = Job(
            id=uuid.uuid4(), owner_id=uuid.UUID(owner_id), url=url,
            status=JobStatus.COMPLETED,
            browser_matrix={"chromium": True, "firefox": False, "webkit": False},
        )
        db.add(job)
        db.commit()
        return str(job.id)
    finally:
        db.close()


def _login(username: str, password: str) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


_uid_a = None
_uid_b = None


def _setup() -> str:
    """Ensure users exist and return a fresh owned job id (suite-order safe)."""
    global _uid_a, _uid_b
    _uid_a = _ensure_user("journey_a", "pw")
    _uid_b = _ensure_user("journey_b", "pw")
    return _make_job(_uid_a, "http://example.com")


def _steps() -> list[dict]:
    return [
        {"action": "goto", "url": "http://example.com"},
        {"action": "assert", "selector": "body", "expect": "visible"},
    ]


def test_journey_crud_and_isolation() -> None:
    _job_a = _setup()
    h = {"Authorization": f"Bearer {_login('journey_a', 'pw')['token']}"}
    hb = {"Authorization": f"Bearer {_login('journey_b', 'pw')['token']}"}
    hc = {**h, "Content-Type": "application/json"}

    r = client.post("/api/journeys", headers=hc, json={
        "job_id": _job_a, "name": "Smoke", "steps": _steps(),
    })
    assert r.status_code == 201, r.text
    jid = r.json()["id"]
    assert r.json()["owner_id"] == _uid_a

    # list as owner
    r = client.get("/api/journeys", headers=h)
    assert any(j["id"] == jid for j in r.json()["journeys"])

    # isolation: user B cannot see or read (404 anti-enumeration)
    r = client.get("/api/journeys", headers=hb)
    assert all(j["id"] != jid for j in r.json()["journeys"])
    r = client.get(f"/api/journeys/{jid}", headers=hb)
    assert r.status_code == 404

    # update
    r = client.patch(f"/api/journeys/{jid}", headers=hc, json={"name": "Smoke v2"})
    assert r.status_code == 200
    assert r.json()["name"] == "Smoke v2"

    # delete (user B cannot)
    r = client.delete(f"/api/journeys/{jid}", headers=hb)
    assert r.status_code == 404
    r = client.delete(f"/api/journeys/{jid}", headers=h)
    assert r.status_code == 200


def test_journey_validation() -> None:
    _job_a = _setup()
    h = {"Authorization": f"Bearer {_login('journey_a', 'pw')['token']}"}
    hc = {**h, "Content-Type": "application/json"}

    # empty steps
    r = client.post("/api/journeys", headers=hc, json={"job_id": _job_a, "name": "x", "steps": []})
    assert r.status_code == 400

    # unknown action
    r = client.post("/api/journeys", headers=hc, json={
        "job_id": _job_a, "name": "x", "steps": [{"action": "explode"}],
    })
    assert r.status_code == 400

    # fill missing selector
    r = client.post("/api/journeys", headers=hc, json={
        "job_id": _job_a, "name": "x", "steps": [{"action": "fill", "value": "y"}],
    })
    assert r.status_code == 400


def test_journey_run_lifecycle() -> None:
    _job_a = _setup()
    h = {"Authorization": f"Bearer {_login('journey_a', 'pw')['token']}"}
    hc = {**h, "Content-Type": "application/json"}

    r = client.post("/api/journeys", headers=hc, json={
        "job_id": _job_a, "name": "RunTest", "steps": _steps(),
    })
    jid = r.json()["id"]

    # run — target is example.com which may not be reachable in test env,
    # but the run should be created and progress to a terminal status.
    r = client.post(f"/api/journeys/{jid}/run", headers=h)
    assert r.status_code == 201, r.text
    run_id = r.json()["run_id"]

    # get run (should exist; status may be running/passed/failed/error)
    r = client.get(f"/api/journeys/{jid}/run/{run_id}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == run_id
    assert r.json()["status"] in ("queued", "running", "passed", "failed", "error")

    # cleanup
    from src.api.app import SessionLocal
    from src.models.database import Journey, JourneyRun

    db = SessionLocal()
    try:
        db.query(JourneyRun).filter_by(journey_id=uuid.UUID(jid)).delete()
        db.query(Journey).filter_by(id=uuid.UUID(jid)).delete()
        db.commit()
    finally:
        db.close()


def test_journeys_require_auth() -> None:
    assert client.get("/api/journeys").status_code == 401
    assert client.post("/api/journeys", json={}).status_code == 401
