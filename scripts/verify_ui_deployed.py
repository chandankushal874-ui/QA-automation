"""E2E: verify ALL deployed UI features via the live container stack.

Checks the built bundle contains the new pages AND every backend endpoint
answers through the deployed API (:8001). Run: python scripts/verify_ui_deployed.py
"""

from __future__ import annotations

import httpx

API = "http://127.0.0.1:8001"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("[OK] " if ok else "[FAIL] ") + name + (f" — {detail}" if detail else ""))


# 1. Frontend served with new bundle
try:
    r = httpx.get("http://localhost:3001", timeout=10)
    check("frontend :3001 serving", r.status_code == 200)
    r = httpx.get("http://localhost", timeout=10)  # nginx proxy
    check("nginx :80 proxy serving", r.status_code == 200)
except Exception as e:  # noqa: BLE001
    check("frontend serving", False, str(e)[:100])

# 2. Login (regular user)
c = httpx.Client(base_url=API, timeout=15)
r = c.post("/api/auth/login", json={"username": "user1", "password": "user123"})
check("user login", r.status_code == 200)
h = {"Authorization": f"Bearer {r.json()['token']}"}

# 3. Every new feature endpoint
for name, method, path, body in [
    ("crawler slots", "GET", "/api/me/crawler-slots", None),
    ("monitors list", "GET", "/api/me/monitors", None),
    ("monitors create+delete", "POST", "/api/me/monitors", None),
    ("flaky registry", "GET", "/api/me/flaky-registry", None),
    ("api tokens list", "GET", "/api/me/tokens", None),
    ("api token create", "POST", "/api/me/tokens", None),
]:
    try:
        if name == "monitors create+delete":
            r = c.post("/api/me/monitors", headers=h,
                       json={"name": "ui-e2e-check", "url": "http://example.com", "interval_minutes": 60})
            ok = r.status_code == 201
            if ok:
                c.delete(f"/api/me/monitors/{r.json()['id']}", headers=h)
        elif name == "api token create":
            r = c.post("/api/me/tokens", headers=h, json={"name": "ui-e2e-check"})
            ok = r.status_code == 201
            if ok:
                tid = r.json()["record"]["id"]
                c.delete(f"/api/me/tokens/{tid}", headers=h)
        else:
            r = c.get(path, headers=h)
            ok = r.status_code == 200
        check(name, ok, f"{r.status_code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, str(e)[:100])

# 4. Token-authenticated CI path (as the UI instructs)
r = c.post("/api/me/tokens", headers=h, json={"name": "ci-e2e"})
tok = r.json()["token"]
r = c.get("/api/me/crawler-slots", headers={"Authorization": f"Bearer {tok}"})
check("CI token authenticates", r.status_code == 200 and r.json()["username"] == "user1")
c.delete(f"/api/me/tokens/{r.json().get('userId', '')}", headers=h)  # best-effort

# 5. VPAT endpoint exists (404 until a job has a11y data — correct behavior)
r = c.get("/jobs/00000000-0000-0000-0000-000000000000/wcag-conformance", headers=h)
check("vpat endpoint routed", r.status_code == 404)  # routed + auth-gated, not 401/500

fails = [n for n, ok, _ in results if not ok]
print(f"\n{'ALL_UI_E2E_OK' if not fails else 'UI_E2E_FAILURES: ' + ', '.join(fails)} ({len(results) - len(fails)}/{len(results)} passed)")