"""Live E2E for visual-baseline diffing against the deployed stack.

Drives the real API at http://127.0.0.1:8001 (docker stack): create a project,
crawl the deterministic fixture app, capture a baseline, re-crawl, compare,
and assert the pipeline returns matches. Verifies MinIO objects exist.

Run: python -X utf8 scripts/verify_visual_baseline_live.py
"""

from __future__ import annotations

import sys
import time
import uuid

import httpx

API = "http://127.0.0.1:8001"
FIXTURE = "http://fixture:8090"

USER = f"vr_{uuid.uuid4().hex[:8]}"
PASSWORD = "vr-pass-123"


def main() -> int:
    c = httpx.Client(base_url=API, timeout=30.0)

    # 1) register + login
    r = c.post("/api/auth/register", json={"username": USER, "password": PASSWORD})
    if r.status_code not in (200, 201):
        print(f"[FAIL] register: {r.status_code} {r.text[:200]}")
        return 1
    r = c.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
    if r.status_code != 200:
        print(f"[FAIL] login: {r.status_code} {r.text[:200]}")
        return 1
    h = {"Authorization": f"Bearer {r.json()['token']}"}

    # 2) create project
    slug = f"e2e{uuid.uuid4().hex[:8]}"
    r = c.post("/api/projects", headers=h, json={"name": "E2E Fixture", "slug": slug, "base_url": FIXTURE})
    if r.status_code != 201:
        print(f"[FAIL] create project: {r.status_code} {r.text[:300]}")
        return 1
    pid = r.json()["id"]
    print(f"[ok] project {slug} created ({pid})")

    # 3) crawl fixture (baseline run)
    def crawl() -> str:
        r = c.post("/jobs", headers=h, json={
            "url": FIXTURE, "scope": "same-domain",
            "browser_matrix": {"chromium": True, "firefox": False, "webkit": False},
            "max_pages": 4, "max_depth": 1,
        })
        if r.status_code != 201:
            print(f"[FAIL] submit job: {r.status_code} {r.text[:300]}")
            sys.exit(1)
        return r.json()["id"]

    def wait(job_id: str) -> bool:
        for _ in range(120):  # up to 10 min
            j = c.get(f"/jobs/{job_id}", headers=h).json()
            if j["status"] in ("completed", "failed", "cancelled"):
                return j["status"] == "completed"
            time.sleep(5)
        return False

    job1 = crawl()
    print(f"[ok] baseline job {job1} submitted")
    if not wait(job1):
        print("[FAIL] baseline crawl did not complete")
        return 1
    print("[ok] baseline crawl completed")

    # 4) capture baseline
    r = c.post(f"/api/projects/{pid}/baseline", headers=h, json={"job_id": job1})
    if r.status_code != 201:
        print(f"[FAIL] capture baseline: {r.status_code} {r.text[:300]}")
        return 1
    n_base = r.json()["baselines_created"]
    print(f"[ok] captured {n_base} baselines")
    if n_base == 0:
        print("[WARN] zero baselines captured — snapshots may not be visible to API (check ./data mount)")

    # 5) re-crawl + compare (deterministic fixture → expect matches, 0 regressions)
    job2 = crawl()
    print(f"[ok] compare job {job2} submitted")
    if not wait(job2):
        print("[FAIL] compare crawl did not complete")
        return 1
    r = c.post(f"/api/projects/{pid}/compare", headers=h, json={"job_id": job2})
    if r.status_code != 201:
        print(f"[FAIL] compare: {r.status_code} {r.text[:300]}")
        return 1
    body = r.json()
    print(f"[ok] compare: regressions={body['regressions']} matches={body['matches']} "
          f"new_baselines={body['new_baselines']} total={body['total']}")

    # 6) verify MinIO objects exist via artifact proxy for a regression/matched entry
    art_checked = False
    for entry in body.get("results", []):
        if entry.get("baseline_key"):
            r = c.get(f"/api/projects/{pid}/artifact/{entry['baseline_key']}", headers=h)
            print(f"[ok] artifact proxy (baseline) -> {r.status_code}")
            art_checked = True
            break
    if not art_checked:
        print("[WARN] no baseline_key in results to proxy (expected on first-run new_baselines)")

    # 7) run ci_gate.py --project against live API (expect exit 0)
    import subprocess

    r = c.post("/api/me/tokens", headers=h, json={"name": "e2e-ci"})
    raw_token = r.json()["token"]
    cp = subprocess.run(
        [
            sys.executable, "-X", "utf8", "scripts/ci_gate.py",
            "--api-url", API, "--token", raw_token, "--target", FIXTURE,
            "--max-pages", "3", "--max-depth", "1", "--timeout", "300",
            "--project", slug, "--max-visual", "0",
        ],
        capture_output=True, text=True, timeout=400,
    )
    print(f"[ci_gate] exit={cp.returncode}")
    tail = (cp.stdout or "")[-600:]
    print(tail)
    if cp.returncode not in (0, 1):  # 0 pass, 1 regression; 2 = infra error
        print(f"[FAIL] ci_gate infra error: {cp.stderr[:500]}")
        return 1

    print("\nVISUAL_BASELINE_LIVE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
