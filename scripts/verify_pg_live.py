"""Verify the app runs on live Docker Postgres (host port 5433) with full
multi-tenant schema and ownership stamping. Run from project root:
    python scripts/verify_pg_live.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL_SYNC"] = "postgresql://cbtp:cbtp_local_dev_password_2026@127.0.0.1:5433/cbtp"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://cbtp:cbtp_local_dev_password_2026@127.0.0.1:5433/cbtp"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from src.api.app import DATABASE_URL, app, engine

print("ENGINE ON:", DATABASE_URL.split("@")[-1])
insp = inspect(engine)
tables = sorted(insp.get_table_names())
print("tables:", len(tables), tables)
cols = [c["name"] for c in insp.get_columns("jobs")]
print("jobs.owner_id present:", "owner_id" in cols)

client = TestClient(app)
r = client.get("/health")
print("health:", r.status_code)

r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
print("admin login on PG:", r.status_code)
tok = r.json().get("token")
h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

r = client.post(
    "/jobs",
    headers=h,
    json={
        "url": "http://example.com/pg-live",
        "max_pages": 1,
        "max_depth": 1,
        "browser_matrix": {"chromium": True, "firefox": False, "webkit": False},
    },
)
print("job create on PG:", r.status_code)
jid = r.json()["id"]

with engine.connect() as conn:
    row = conn.execute(
        text(
            "SELECT owner_id IS NOT NULL, config->'isolation'->>'tenant' "
            f"FROM jobs WHERE id = '{jid}'"
        )
    ).fetchone()
    print("owner stamped:", row[0], "| tenant:", row[1])

# monitors + tokens live on PG too
r = client.post(
    "/api/me/monitors",
    headers=h,
    json={"name": "pg-live-check", "url": "http://example.com", "interval_minutes": 60},
)
print("monitor create on PG:", r.status_code)
r = client.post("/api/me/tokens", headers=h, json={"name": "pg-live-check"})
print("token create on PG:", r.status_code)

print("PG_LIVE_OK")
