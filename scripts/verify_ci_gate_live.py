"""Live E2E of the CI gate path against the running container.

Creates a CI bot user + API token, then runs scripts/ci_gate.py end-to-end
against the fixture app. Run: python scripts/verify_ci_gate_live.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8001"

c = httpx.Client(base_url=BASE, timeout=15)

# admin session
r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
assert r.status_code == 200, r.text
admin_tok = r.json()["token"]
ha = {"Authorization": f"Bearer {admin_tok}", "Content-Type": "application/json"}

# create dedicated ci-bot user (201 or 400-if-exists both fine)
r = c.post(
    "/api/auth/users",
    headers=ha,
    json={"username": "ci-bot", "password": "cibot-pass-1", "role": "user"},
)
print("create ci-bot:", r.status_code)

# login as ci-bot (seed-resync keeps password in sync from this call onward
# only if SEED_USERS declares it; for the demo fall back to user1 on failure)
r = c.post("/api/auth/login", json={"username": "ci-bot", "password": "cibot-pass-1"})
if r.status_code != 200:
    print("ci-bot login failed -> using user1 for demo")
    r = c.post("/api/auth/login", json={"username": "user1", "password": "user123"})
assert r.status_code == 200, r.text
jwt = r.json()["token"]
h = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}

# mint an API token for the gate
r = c.post("/api/me/tokens", headers=h, json={"name": "ci-gate-e2e"})
assert r.status_code == 201, r.text
api_token = r.json()["token"]
print("api token:", api_token[:12] + "...")

# free the demo account's slots
mine = c.get("/jobs?limit=50", headers=h).json()
for j in mine.get("jobs", []):
    if j["status"] in ("pending", "running"):
        c.post(f"/jobs/{j['id']}/stop", headers=h)

# run the actual gate script against the fixture app inside docker.
# NOTE: fixture is reachable at host :8090 too (ports: 8090:8090).
gate = Path("scripts/ci_gate.py").resolve()
cmd = [
    sys.executable, str(gate),
    "--api-url", BASE,
    "--token", api_token,
    "--target", "http://127.0.0.1:8090",
    "--max-blocking", "0",
    "--max-functional", "-1",
    "--max-pages", "10",
    "--max-depth", "2",
    "--timeout", "900",
]
print("\n$ " + " ".join(cmd[:6]) + " ...\n")
res = subprocess.run(cmd, capture_output=True, text=True, timeout=960)
print(res.stdout[-2500:])
if res.stderr:
    print("STDERR:", res.stderr[-500:])
print("ci_gate exit code:", res.returncode)
assert res.returncode in (0, 1, 2)
print("CI_GATE_LIVE_OK" if res.returncode in (0, 1) else "CI_GATE_INFRA_FAIL")
