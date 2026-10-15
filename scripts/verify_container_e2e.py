"""E2E verification through the containerized API (host port 8001).

Proves the deployed app — real Postgres, real Redis, real RQ dispatch —
enforces multi-user isolation, 2-slot limits, tokens, and monitors.
Run: python scripts/verify_container_e2e.py
"""

from __future__ import annotations

import httpx

BASE = "http://127.0.0.1:8001"

c = httpx.Client(base_url=BASE, timeout=15)

r = c.get("/health")
assert r.status_code == 200, r.text
print("container health:", r.json()["status"])

r1 = c.post("/api/auth/login", json={"username": "user1", "password": "user123"})
assert r1.status_code == 200, r1.text
r2 = c.post("/api/auth/login", json={"username": "user2", "password": "user123"})
assert r2.status_code == 200, r2.text
t1, t2 = r1.json()["token"], r2.json()["token"]
h1 = {"Authorization": f"Bearer {t1}", "Content-Type": "application/json"}
h2 = {"Authorization": f"Bearer {t2}"}

# Idempotency: stop user1's active jobs so re-runs start with both slots free.
mine = c.get("/jobs?limit=50", headers=h1).json()
for j in mine.get("jobs", []):
    if j["status"] in ("pending", "running"):
        c.post(f"/jobs/{j['id']}/stop", headers=h1)

body = {
    "url": "http://fixture:8090/iso-a",
    "max_pages": 1,
    "max_depth": 1,
    "browser_matrix": {"chromium": True, "firefox": False, "webkit": False},
}
ja = c.post("/jobs", headers=h1, json=body)
jb = c.post("/jobs", headers=h1, json=dict(body, url="http://fixture:8090/iso-b"))
jc = c.post("/jobs", headers=h1, json=dict(body, url="http://fixture:8090/iso-c"))
print("user1 jobs:", ja.status_code, jb.status_code, "| 3rd:", jc.status_code, "(expect 429)")
assert ja.status_code == jb.status_code == 201 and jc.status_code == 429

l2 = c.get("/jobs", headers=h2).json()
assert all(j["id"] != ja.json()["id"] for j in l2["jobs"]), "isolation leak!"
print("user2 visible jobs:", l2["total"], "(isolation OK)")

m = c.post(
    "/api/me/monitors",
    headers=h1,
    json={"name": "e2e", "url": "http://example.com", "interval_minutes": 30},
)
t = c.post("/api/me/tokens", headers=h1, json={"name": "e2e-ci"})
print("monitor:", m.status_code, "| token:", t.status_code)
assert m.status_code == 201 and t.status_code == 201

r = c.get("/api/me/crawler-slots", headers={"Authorization": f"Bearer {t.json()['token']}"})
assert r.status_code == 200 and r.json()["username"] == "user1", r.text
print("API-token auth via container: OK")

print("CONTAINER_E2E_OK")
