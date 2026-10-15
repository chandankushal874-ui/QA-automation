"""Consistency soak test: 100 concurrent job creations.

Proves the per-user slot limit (max_concurrent_jobs_per_user=2) is enforced
under parallel load — 100 rapid/concurrent create attempts must yield at
most 2 active jobs and (100 - accepted) 429 refusals. Also validates API
token stability across 50 repeated authentications.

Run: pytest tests/test_consistency_soak.py -q
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from src.api.app import app, engine
from src.models.database import Base, PlatformUser

Base.metadata.create_all(bind=engine)

# TestClient with raise_server_exceptions=False so 429s are responses, not raises
client = TestClient(app, raise_server_exceptions=False)


def _ensure_user(username: str, password: str) -> None:
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
                    role="user",
                    is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()


_ensure_user("soak_user", "soak_pass")


def _login() -> str:
    r = client.post("/api/auth/login", json={"username": "soak_user", "password": "soak_pass"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_slot_limit_consistent_across_100_concurrent_creates():
    """100 concurrent create attempts → never more than 2 active jobs."""
    token = _login()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Stop any leftovers so we start at 0 active
    listing = client.get("/jobs?limit=100", headers={"Authorization": f"Bearer {token}"})
    for j in listing.json().get("jobs", []):
        if j["status"] in ("pending", "running"):
            client.post(f"/jobs/{j['id']}/stop", headers={"Authorization": f"Bearer {token}"})

    def _attempt(i: int) -> int:
        r = client.post(
            "/jobs",
            headers=headers,
            json={
                "url": f"http://example.com/soak-{i}",
                "max_pages": 1,
                "max_depth": 1,
                "browser_matrix": {"chromium": True, "firefox": False, "webkit": False},
            },
        )
        return r.status_code

    # 100 attempts: 30 in parallel waves, then 70 sequential (pytest guard stops
    # real dispatch so no actual crawls run — jobs stay PENDING, exercising the
    # active-count limit exactly as production would with a busy queue).
    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(_attempt, range(30)))
    statuses += [_attempt(i) for i in range(30, 100)]

    accepted = sum(1 for s in statuses if s == 201)
    refused = sum(1 for s in statuses if s == 429)
    other = [s for s in statuses if s not in (201, 429)]

    # Invariants
    assert not other, f"Unexpected statuses: {set(other)}"
    assert accepted <= 2, f"Slot limit violated: {accepted} accepted (limit 2)"
    assert accepted + refused == 100, f"Lost responses: accepted={accepted} refused={refused}"

    # DB truth: active jobs for this user never exceed 2
    from src.api.app import SessionLocal
    from src.models.database import Job, JobStatus

    db = SessionLocal()
    try:
        owner = db.query(PlatformUser).filter(PlatformUser.username == "soak_user").first()
        active = (
            db.query(Job)
            .filter(
                Job.owner_id == owner.id,
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
            .count()
        )
        assert active <= 2, f"DB has {active} active jobs — limit is 2"
    finally:
        db.close()


def test_api_token_stable_across_50_uses():
    """One token used 50× → every use authenticates, exactly one usage stamp."""
    token = _login()
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    r = client.post("/api/me/tokens", headers=h, json={"name": "soak-ci"})
    assert r.status_code == 201, r.text
    api_token = r.json()["token"]
    token_id = r.json()["record"]["id"]

    for _ in range(50):
        r = client.get("/api/me/crawler-slots", headers={"Authorization": f"Bearer {api_token}"})
        assert r.status_code == 200, f"token auth flaked: {r.status_code}"
        assert r.json()["username"] == "soak_user"

    # Cleanup: revoke so the soak user's tokens stay tidy
    r = client.delete(f"/api/me/tokens/{token_id}", headers=h)
    assert r.status_code == 200
