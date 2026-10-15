"""In-process live test: crawl the fixture with CrawlEngine and verify the
generated HTML report contains the Lighthouse card + per-page LH badges.

Runs a REAL 2-page crawl via CrawlEngine (chromium). Verifies end-to-end:
browser perf collection -> registry endpoint -> report generation.

Run: python scripts/verify_lighthouse_live.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL_SYNC"] = "sqlite:///./cbtp_lh_live.db"
os.environ["REDIS_URL"] = "redis://127.0.0.1:9/0"  # force MockRedis

import asyncio
import logging

logging.basicConfig(level=logging.INFO)

if os.path.exists("cbtp_lh_live.db"):
    os.remove("cbtp_lh_live.db")

import uuid  # noqa: E402

from src.api.app import SessionLocal, engine  # noqa: E402
from src.api.auth import hash_password  # noqa: E402
from src.core.mock_redis import MockRedis  # noqa: E402
from src.models.database import Base, Job, JobStatus, PlatformUser, UserRole  # noqa: E402

Base.metadata.create_all(bind=engine)

db = SessionLocal()
owner = PlatformUser(
    id=uuid.uuid4(), username=f"lh_live_{uuid.uuid4().hex[:6]}",
    email="lh@l", password_hash=hash_password("x"),
    role=UserRole.USER.value, is_active=True,
)
db.add(owner)
job = Job(
    id=uuid.uuid4(), owner_id=owner.id, url="http://127.0.0.1:8090",
    status=JobStatus.PENDING,
    browser_matrix={"chromium": True, "firefox": False, "webkit": False},
    max_pages=3, max_depth=1, max_time_seconds=180,
)
db.add(job)
db.commit()
job_id = str(job.id)
db.close()

from src.crawler.engine import CrawlEngine  # noqa: E402

if sys.platform == "win32":
    loop = asyncio.ProactorEventLoop()
else:
    loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)


registry = None


async def main() -> None:
    global registry
    db2 = SessionLocal()
    eng = CrawlEngine(db_session=db2, redis_client=MockRedis(), job_id=job_id)
    registry = eng.registry
    await eng.execute()
    db2.close()


loop.run_until_complete(main())
loop.close()

# DEBUG: inspect what the registry endpoints actually hold
for ep in registry.get_all():
    pd = getattr(ep, "performance_data", None)
    if pd:
        print("PERF DATA:", ep.url, "->", {k: sorted(v.keys()) for k, v in pd.items()})
print("SEED lookup:", registry.get("http://127.0.0.1:8090", "GET") is not None)
print("root-ish urls:", [e.url for e in registry.get_all() if e.url.endswith(":8090")][:5])
print("methods seen:", sorted({e.method for e in registry.get_all()})[:6])
print("first 3 urls:", [e.url for e in registry.get_all()[:3]])
print("endpoints with perf_data:", sum(1 for e in registry.get_all() if getattr(e, "performance_data", None)))
print("total endpoints:", len(registry.get_all()))

db = SessionLocal()
from src.models.database import Issue  # noqa: E402

job = db.query(Job).filter_by(id=uuid.UUID(job_id)).first()
issues = db.query(Issue).filter_by(job_id=job.id).all()
from src.analysis.report_generator import generate_static_html_report  # noqa: E402

path = generate_static_html_report(job_id, job, registry, issues)
db.close()
with open(path, encoding="utf-8") as f:
    html_text = f.read()

print("report:", path, f"({len(html_text)} bytes)")
has_card = "Performance (Lighthouse-style)" in html_text
badges = re.findall(r"LH\*? Perf: <strong[^>]*>(\d+)</strong>", html_text)
has_ttfb = "TTFB" in html_text
print("LH summary card:", has_card)
print("Per-page LH badges:", badges)
print("TTFB present:", has_ttfb)

ok = has_card and has_ttfb and len(badges) > 0
print("LH_LIVE_OK" if ok else "LH_LIVE_MISSING")
sys.exit(0 if ok else 1)
