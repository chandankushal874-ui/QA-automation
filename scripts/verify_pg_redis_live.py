"""Verify the app runs with REAL Postgres (:5433) + REAL Redis (:6380).

Proves: no SQLite/MockRedis fallbacks, slot locks hit Redis, scheduler
leader lock works, tenant Redis DB mapping live. Run: python scripts/verify_pg_redis_live.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL_SYNC"] = "postgresql://cbtp:cbtp_local_dev_password_2026@127.0.0.1:5433/cbtp"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://cbtp:cbtp_local_dev_password_2026@127.0.0.1:5433/cbtp"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6380/0"

import redis
from fastapi.testclient import TestClient

from src.api.app import DATABASE_URL, _acquire_user_slot_lock, app
from src.config import settings

ok = True

# 1. Redis reachable and modern
r = redis.Redis.from_url(settings.redis_url, socket_timeout=3)
assert r.ping(), "Redis unreachable"
ver = r.info("server")["redis_version"]
print(f"[OK] Real Redis {ver} at {settings.redis_url}")

# 2. Slot lock uses REAL Redis NX
r.delete("cbtp:slot-lock:probe-owner")
release = _acquire_user_slot_lock("probe-owner", timeout_seconds=2)
assert release is not None, "slot lock returned None on real Redis"
second = r.set("cbtp:slot-lock:probe-owner", "intruder", nx=True, ex=10)
assert second is None, "NX lock not mutual-exclusive on real Redis"
release()
assert r.set("cbtp:slot-lock:probe-owner", "after-release", nx=True, ex=10)
r.delete("cbtp:slot-lock:probe-owner")
print("[OK] Per-user slot lock: Redis NX mutual exclusion verified")

# 3. Scheduler leader lock
from src.api.monitors import SCHEDULER_LOCK_KEY, _acquire_leader_lock, _release_leader_lock  # noqa: E402

r.delete(SCHEDULER_LOCK_KEY)
assert _acquire_leader_lock() is True, "leader lock not acquired"
assert r.set(SCHEDULER_LOCK_KEY, "other", nx=True, ex=10) is None, "leader lock not exclusive"
_release_leader_lock()
assert r.set(SCHEDULER_LOCK_KEY, "reacquired", nx=True, ex=10) is not None
r.delete(SCHEDULER_LOCK_KEY)
print("[OK] Scheduler leader election: acquire/exclusive/release verified")

# 4. Tenant Redis DB mapping (1..15)
import uuid  # noqa: E402

from src.core.tenant_isolation import redis_url_for_owner  # noqa: E402

u1, u2 = uuid.uuid4(), uuid.uuid4()
p1 = redis_url_for_owner(settings.redis_url, u1)
assert p1.endswith(tuple(str(i) for i in range(1, 16))), f"unexpected tenant db url {p1}"
assert p1 != settings.redis_url, "tenant db must differ from shared db 0"
print(f"[OK] Tenant Redis DB mapping: {p1.split('@')[-1]}")

# 5. App boots on real PG — no SQLite fallback
assert "postgresql" in DATABASE_URL, f"app fell back to {DATABASE_URL}"
client = TestClient(app)
health = client.get("/health").json()
assert health["status"] == "healthy"
print(f"[OK] App on real Postgres: {DATABASE_URL.split('@')[-1]}")

# 6. End-to-end: login → job → token (Redis-backed dispatch path)
resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
assert resp.status_code == 200, resp.text
tok = resp.json()["token"]
h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
resp = client.post(
    "/jobs",
    headers=h,
    json={"url": "http://example.com/redis-live", "max_pages": 1, "max_depth": 1,
          "browser_matrix": {"chromium": True, "firefox": False, "webkit": False}},
)
assert resp.status_code == 201, resp.text
print(f"[OK] Job created: {resp.json()['id'][:8]} (Redis-backed dispatch)")

resp = client.post("/api/me/tokens", headers=h, json={"name": "redis-live"})
assert resp.status_code == 201
print(f"[OK] API token created: {resp.json()['token'][:12]}...")

queue_len = r.llen("rq:queue:default")
print(f"[OK] RQ queue on real Redis: rq:queue:default len={queue_len}")

print("\nALL_LIVE_SERVICES_OK")
