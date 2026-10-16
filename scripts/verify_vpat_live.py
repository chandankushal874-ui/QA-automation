"""In-process live test: crawl fixture -> WCAG/VPAT exports generated + API.

Run: python scripts/verify_vpat_live.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL_SYNC"] = "sqlite:///./cbtp_vpat_live.db"
os.environ["REDIS_URL"] = "redis://127.0.0.1:9/0"

import asyncio  # noqa: E402
import logging  # noqa: E402
import uuid  # noqa: E402

logging.basicConfig(level=logging.INFO)

if os.path.exists("cbtp_vpat_live.db"):
    os.remove("cbtp_vpat_live.db")

from src.core.mock_redis import MockRedis  # noqa: E402
from src.models.database import (  # noqa: E402
    Base, Issue, Job, JobStatus, PlatformUser, UserRole,
)
from src.api.app import SessionLocal, engine  # noqa: E402
from src.api.auth import hash_password  # noqa: E402

Base.metadata.create_all(bind=engine)

db = SessionLocal()
owner = PlatformUser(
    id=uuid.uuid4(), username=f"vpat_{uuid.uuid4().hex[:6]}",
    email="v@l", password_hash=hash_password("pw"),
    role=UserRole.USER.value, is_active=True,
)
db.add(owner)
owner_id = owner.id
job = Job(
    id=uuid.uuid4(), owner_id=owner_id, url="http://127.0.0.1:8090",
    status=JobStatus.PENDING,
    browser_matrix={"chromium": True, "firefox": False, "webkit": False},
    max_pages=4, max_depth=1, max_time_seconds=180,
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

a11y_reports = {}


async def main() -> None:
    db2 = SessionLocal()
    eng = CrawlEngine(db_session=db2, redis_client=MockRedis(), job_id=job_id)
    a11y_reports.update(eng.a11y_results)
    await eng.execute()
    a11y_reports.update(eng.a11y_results)
    db2.close()


loop.run_until_complete(main())
loop.close()

print("a11y pages audited:", len(a11y_reports))

# VPAT files must exist in the job's tenant folder
from src.core.tenant_isolation import job_artifact_dirs  # noqa: E402

paths = job_artifact_dirs(owner_id, uuid.UUID(job_id))
job_root = paths["job_root"]
vpat_md = Path(job_root) / f"vpat_acr_{job_id}.md"
vpat_html = Path(job_root) / f"vpat_acr_{job_id}.html"
conf_json = Path(job_root) / f"wcag_conformance_{job_id}.json"
print("vpat.md:", vpat_md.exists(), "| vpat.html:", vpat_html.exists(), "| json:", conf_json.exists())

if vpat_md.exists():
    md = vpat_md.read_text(encoding="utf-8")
    print("markdown ACR rows:", md.count("| 1."))
    print("has Does Not Support:", "Does Not Support" in md)

ok = vpat_md.exists() and vpat_html.exists() and conf_json.exists()
print("VPAT_LIVE_OK" if ok else "VPAT_LIVE_MISSING")
sys.exit(0 if ok else 1)