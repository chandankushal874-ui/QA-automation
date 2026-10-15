"""Verify Lighthouse scores in a container-generated report (host :8001)."""

from __future__ import annotations

import re

import httpx

c = httpx.Client(base_url="http://127.0.0.1:8001", timeout=30)
r = c.post("/api/auth/login", json={"username": "user1", "password": "user123"})
assert r.status_code == 200, r.text
token = r.json()["token"]
h = {"Authorization": f"Bearer {token}"}

rep = c.get("/jobs/00b23650-0000-0000-0000-000000000000/report/download", headers=h)
# find the real newest job instead (id above is partial)
jobs = c.get("/jobs?limit=5", headers=h).json()["jobs"]
jid = jobs[0]["id"]
print("checking job:", jid[:8], jobs[0]["status"])

rep = c.get(f"/jobs/{jid}/report/download", headers=h)
if rep.status_code != 200:
    # maybe not that job; iterate jobs until one has a downloadable report
    for j in jobs:
        rep = c.get(f"/jobs/{j['id']}/report/download", headers=h)
        if rep.status_code == 200:
            jid = j["id"]
            break
assert rep.status_code == 200, "no downloadable report"

html_text = rep.text
badges = re.findall(r"LH\*? Perf: <strong[^>]*>(\d+)</strong>", html_text)
has_card = "Performance (Lighthouse-style)" in html_text
has_ttfb = "TTFB" in html_text
print("LH summary card:", has_card)
print("Per-page LH badges:", badges)
print("TTFB present:", has_ttfb)

assert has_card and has_ttfb and len(badges) > 0, "Lighthouse data missing from report"
print("CONTAINER_LH_OK")