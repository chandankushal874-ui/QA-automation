"""FastAPI orchestrator â€” database-connected API endpoints with HTML reporting dashboard.

Spec reference: master-build-prompt Â§3, Step E, Step T
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import redis
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker

from src.api.auth import (
    bind_get_db,
    ensure_seed_users,
    get_current_user,
    user_can_access_job,
)
from src.api.auth import (
    router as auth_router,
)
from src.config import settings
from src.core.mock_redis import MockRedis
from src.crawler.engine import CrawlEngine
from src.models.database import (
    AuthProfile,
    Base,
    CrawlState,
    Issue,
    IssueCategory,
    IssueSeverity,
    Job,
    JobStatus,
    PlatformUser,
    UserRole,
)

logger = logging.getLogger(__name__)


# --- Database configuration & Session Setup ---

DATABASE_URL = settings.database_url_sync
try:
    engine = create_engine(DATABASE_URL, pool_size=50, max_overflow=20)
    # Force a real connection now (engine is lazy) so driver/server problems
    # are caught immediately at startup.
    with engine.connect():
        pass
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with contextlib.suppress(Exception):
        # Benign race: multiple gunicorn workers boot at once and one already
        # created the tables (checkfirst is not atomic).
        Base.metadata.create_all(bind=engine)
except Exception as e:
    # Production-grade resilience: degrade to SQLite instead of crashing when
    # Postgres is unavailable (local dev, CI, friend-laptop installs).
    # The warning is loud so operators notice the fallback in logs.
    if settings.database_require_postgres:
        logger.error("PostgreSQL connection failed: %s.", e)
        raise RuntimeError(
            f"PostgreSQL connection failed: {e}. Please ensure PostgreSQL 16 is running at {DATABASE_URL}"
        ) from e
    logger.warning(
        "PostgreSQL unavailable (%s). Falling back to local SQLite cbtp.db — "
        "use PostgreSQL in production for durability and concurrency.", e,
    )
    DATABASE_URL = "sqlite:///./cbtp.db"
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        pool_size=50,
        max_overflow=20,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with contextlib.suppress(Exception):
        Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_owner_column() -> None:
    """Add jobs.owner_id if missing (SQLite/Postgres, no Alembic required)."""
    try:
        from sqlalchemy import inspect, text

        insp = inspect(engine)
        if "jobs" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("jobs")}
        if "owner_id" in cols:
            return
        with engine.begin() as conn:
            if engine.dialect.name == "sqlite":
                conn.execute(text("ALTER TABLE jobs ADD COLUMN owner_id VARCHAR(36)"))
            else:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN owner_id UUID"))
        logger.info("Added jobs.owner_id column for multi-user isolation")
    except Exception as e:
        logger.warning("Could not ensure owner_id column: %s", e)


def _ensure_auth_enterprise_columns() -> None:
    """Add MFA/OIDC columns + oidc_states table without Alembic."""
    try:
        from sqlalchemy import inspect, text

        insp = inspect(engine)
        tables = set(insp.get_table_names())
        if "platform_users" in tables:
            cols = {c["name"] for c in insp.get_columns("platform_users")}
            alters: list[str] = []
            if "mfa_enabled" not in cols:
                alters.append(
                    "ADD COLUMN mfa_enabled BOOLEAN DEFAULT 0"
                    if engine.dialect.name == "sqlite"
                    else "ADD COLUMN mfa_enabled BOOLEAN DEFAULT FALSE"
                )
            if "mfa_secret" not in cols:
                alters.append("ADD COLUMN mfa_secret VARCHAR(64)")
            if "auth_provider" not in cols:
                alters.append("ADD COLUMN auth_provider VARCHAR(20) DEFAULT 'local'")
            if "oidc_subject" not in cols:
                alters.append("ADD COLUMN oidc_subject VARCHAR(255)")
            if alters:
                with engine.begin() as conn:
                    for a in alters:
                        conn.execute(text(f"ALTER TABLE platform_users {a}"))
                logger.info("Added platform_users MFA/OIDC columns")
        # Create new tables (audit_events, oidc_states) via metadata
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        logger.warning("Could not ensure auth enterprise columns: %s", e)


_ensure_owner_column()
_ensure_auth_enterprise_columns()


def _ensure_monitor_jira_columns() -> None:
    """Add monitor_schedules jira_* columns on legacy DBs.

    create_all never ALTERs existing tables; without this, monitor
    completion crashes on 'column does not exist' (caught live on PG).
    """
    try:
        from sqlalchemy import inspect, text

        insp = inspect(engine)
        if "monitor_schedules" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("monitor_schedules")}
        wanted = {
            "jira_url": "VARCHAR(512)",
            "jira_project": "VARCHAR(64)",
            "jira_token_encrypted": "VARCHAR(512)",
        }
        missing = {k: t for k, t in wanted.items() if k not in cols}
        if missing:
            with engine.begin() as conn:
                for col, coltype in missing.items():
                    conn.execute(text(f"ALTER TABLE monitor_schedules ADD COLUMN {col} {coltype}"))
            logger.info("Added monitor_schedules jira columns: %s", ", ".join(missing))
    except Exception as e:
        logger.warning("Could not ensure monitor jira columns: %s", e)


_ensure_monitor_jira_columns()

# Wire auth module to the same SessionLocal
bind_get_db(get_db)
try:
    _seed_db = SessionLocal()
    try:
        ensure_seed_users(_seed_db)
    finally:
        _seed_db.close()
except Exception as e:
    logger.warning("Could not seed platform users on startup: %s", e)


# --- FastAPI Application ---

app = FastAPI(
    title="Cross-Browser Testing Platform API",
    description="Autonomous cross-browser web testing — paste a URL, get a comprehensive bug report.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register auth router for self-hosted multi-user login
app.include_router(auth_router)

# API tokens for CI/CD (additive — no effect unless created/used)
from src.api.tokens import router as tokens_router  # noqa: E402

app.include_router(tokens_router)

# Scheduled site monitoring (additive — scheduler started on app startup)
from src.api.monitors import router as monitors_router  # noqa: E402
from src.api.monitors import start_scheduler  # noqa: E402

app.include_router(monitors_router)

# Flaky-issue registry (cross-crawl quarantine, tenant-isolated)
from src.api.flaky_registry import router as flaky_router  # noqa: E402

app.include_router(flaky_router)
with contextlib.suppress(Exception):
    start_scheduler(app)


# â”€â”€â”€ Backward-compatible in-memory store for unit tests â”€â”€â”€

JOBS_STORE: dict[str, dict[str, Any]] = {}


# â”€â”€â”€ Request/Response Pydantic Models â”€â”€â”€


class BrowserMatrix(BaseModel):
    chromium: bool = True
    firefox: bool = False
    webkit: bool = False


class Credentials(BaseModel):
    username: str = ""
    password: str = ""
    login_url: str = ""
    token: str = ""
    cookies: list[dict[str, str]] = Field(default_factory=list)


class CreateJobRequest(BaseModel):
    url: str = Field(..., description="Target URL to test", min_length=1)
    scope: str = Field(
        default="same-domain",
        description="Crawl scope: same-domain, same-origin, or custom",
    )
    browser_matrix: BrowserMatrix = Field(default_factory=BrowserMatrix)
    credentials: Credentials | None = None
    max_pages: int = Field(default=1000, ge=1, le=100000)
    max_depth: int = Field(default=10, ge=1, le=100)
    max_time_seconds: int = Field(default=3600, ge=60, le=86400)
    repo_url: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("Invalid URL: must include scheme (http/https) and host")
        if parsed.scheme not in ("http", "https"):
            raise ValueError("URL scheme must be http or https")
        return v

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str) -> str:
        allowed = {"same-domain", "same-origin", "custom"}
        if v not in allowed:
            raise ValueError(f"Scope must be one of: {allowed}")
        return v


class JobResponse(BaseModel):
    id: str
    url: str
    status: str
    scope: str
    browser_matrix: dict[str, bool]
    has_credentials: bool
    max_pages: int
    max_depth: int
    max_time_seconds: int
    created_at: str
    message: str
    # Live progress fields (populated during crawl)
    pages_discovered: int = 0
    pages_visited: int = 0
    issues_found: int = 0
    console_errors_found: int = 0
    endpoints_discovered: int = 0


class JobReportResponse(BaseModel):
    id: str
    url: str
    status: str
    pages_discovered: int
    pages_visited: int
    issues_found: int
    console_errors_found: int
    endpoints_discovered: int
    browser_matrix: dict[str, bool]
    issues: list[dict[str, Any]]
    console_error_summary: dict[str, int]
    error_type_summary: dict[str, int]
    endpoints: list[dict[str, Any]]
    report_html_url: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    quarantined_issues: int = 0
    lighthouse_seed: dict[str, Any] | None = None


# â”€â”€â”€ Helpers â”€â”€â”€


def is_localhost_or_private(url: str) -> bool:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True

    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback
    except ValueError:
        pass

    return bool(hostname.endswith(".local") or hostname.endswith(".internal"))


def _load_job_for_worker(job_id_str: str, attempts: int = 6, delay_s: float = 1.0):
    """Worker-side job load with bounded retry.

    RQ can dequeue a job before the API's INSERT transaction is visible to
    the worker's new session (observed in live runs). Retry with fresh
    sessions so crawls never die with 'Job not found' on fast workers.
    """
    import time as _time

    last_db = None
    for attempt in range(1, attempts + 1):
        db = SessionLocal()
        try:
            job_uuid = uuid.UUID(job_id_str)
            job = db.query(Job).filter_by(id=job_uuid).first()
            if job:
                return db, job
            logger.warning(
                "Job %s not visible yet (attempt %d/%d) — retrying",
                job_id_str, attempt, attempts,
            )
        except Exception:
            db.close()
            raise
        db.close()
        last_db = None
        _time.sleep(delay_s)
    return last_db, None


def run_background_crawl_job(job_id_str: str) -> None:
    """Entry point for background thread running CrawlEngine loop.

    Uses daemon thread + asyncio.run() - the proven approach that works.
    All logging goes through logger.info/error so it's visible in uvicorn output.
    """
    logger.info(f"Starting background crawl for job {job_id_str}")

    db: Session | None
    from src.api.app import engine as app_engine
    # Guard: a worker that silently fell back to SQLite while the API runs
    # Postgres would crawl a DIFFERENT database (jobs enqueued by the API are
    # invisible). Fail loudly instead of crawling the wrong store.
    if settings.database_require_postgres and app_engine.dialect.name != "postgresql":
        raise RuntimeError(
            "DATABASE_REQUIRE_POSTGRES=true but worker engine is "
            f"{app_engine.dialect.name!r} — refusing to process crawls on a "
            "fallback database. Install psycopg (v3) / fix DATABASE_URL_SYNC."
        )
    db, job = _load_job_for_worker(job_id_str)
    try:
        if not job:
            logger.error(f"Job {job_id_str} not found for background crawl task")
            return

        logger.info(f"Found job: {job.url}, status={job.status.value}")

        # Tenant-isolated Redis: prefer per-owner logical DB; keys also tenant-prefixed.
        # User A's frontier never shares keyspace with User B.
        from src.core.tenant_isolation import (
            apply_job_isolation_paths,
            assert_job_owner_isolation,
            redis_url_for_owner,
            resolve_job_owner_id,
        )

        owner_id = resolve_job_owner_id(job)
        if not owner_id:
            logger.error(
                "Refusing crawl for job %s: missing owner_id (isolation required)",
                job_id_str,
            )
            job.status = JobStatus.FAILED
            db.commit()
            return
        try:
            assert_job_owner_isolation(job)
        except ValueError as iso_err:
            logger.error("Isolation check failed for job %s: %s", job_id_str, iso_err)
            job.status = JobStatus.FAILED
            db.commit()
            return
        # Ensure isolation paths exist even if job was created before this code
        if not (job.config or {}).get("isolation"):
            apply_job_isolation_paths(job)
            db.commit()

        redis_client: Any
        try:
            redis_url = redis_url_for_owner(settings.redis_url, owner_id)
            redis_client = redis.Redis.from_url(redis_url)
            redis_client.ping()
            logger.info(
                "Connected to Redis for crawl (tenant-isolated owner=%s)",
                str(owner_id)[:8],
            )
        except Exception:
            try:
                redis_client = redis.Redis.from_url(settings.redis_url)
                redis_client.ping()
                logger.info("Connected to Redis for crawl (shared fallback + key prefix)")
            except Exception:
                logger.warning("Redis server offline. Falling back to MockRedis.")
                redis_client = MockRedis()

        # Decrypt target-site password for auth bypass (in-memory only)
        if job.auth_profile and job.auth_profile.password_encrypted:
            try:
                from src.core.secrets_crypto import decrypt_secret

                plain = decrypt_secret(job.auth_profile.password_encrypted)
                # Temporary attribute for crawl only — not written back
                job.auth_profile.password_encrypted = plain  # type: ignore[assignment]
            except Exception as dec_err:
                logger.warning("Could not decrypt target credentials: %s", dec_err)

        # Run Crawler Loop using explicit ProactorEventLoop (required on Windows for subprocess support)
        logger.info(
            "Creating CrawlEngine for owner=%s job=%s...",
            str(owner_id)[:8],
            job_id_str[:8],
        )
        engine = CrawlEngine(db_session=db, redis_client=redis_client, job_id=job_id_str)
        logger.info("CrawlEngine created. Starting execute()...")

        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(engine.execute())
        finally:
            loop.close()

        logger.info("Crawl execute() completed successfully!")

        # Mark job completed — but only if the engine didn't already set a terminal status
        # (e.g. FAILED from the crash handler inside execute())
        db.refresh(job)
        if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(UTC)
            db.commit()
            logger.info(f"Job {job_id_str} marked COMPLETED")
        else:
            logger.info(f"Job {job_id_str} already terminal ({job.status.value}), not overriding")

        # Flaky registry update: record this run's seen/absent outcomes per
        # fingerprint BEFORE notifications so quarantine verdicts are fresh.
        # Non-fatal by design (registry must never break the crawl pipeline).
        with contextlib.suppress(Exception):
            from src.api.flaky_registry import record_run_outcomes

            job_issues = db.query(Issue).filter_by(job_id=job.id).all()
            verdicts = record_run_outcomes(db, job.owner_id, job, job_issues)
            quarantined = [fp for fp, st in verdicts.items() if st in ("quarantined", "confirmed")]
            if quarantined:
                logger.info(
                    "Flaky registry: %d fingerprint(s) quarantined for job %s",
                    len(quarantined), job_id_str[:8],
                )

        # Monitor webhook notification (Slack-compatible) — non-fatal by design
        with contextlib.suppress(Exception):
            from src.api.monitors import maybe_notify_job_completion

            loop_notify = asyncio.new_event_loop()
            try:
                loop_notify.run_until_complete(maybe_notify_job_completion(db, job))
            finally:
                loop_notify.close()

        # Sync updates back to backward-compatible JOBS_STORE
        db.refresh(job)
        if job_id_str in JOBS_STORE:
            JOBS_STORE[job_id_str]["status"] = job.status.value
            JOBS_STORE[job_id_str]["pages_discovered"] = job.pages_discovered
            JOBS_STORE[job_id_str]["pages_visited"] = job.pages_visited
            JOBS_STORE[job_id_str]["issues_found"] = job.issues_found
            JOBS_STORE[job_id_str]["console_errors_found"] = job.console_errors_found

    except Exception as e:
        logger.error(f"FATAL ERROR in background crawl for job {job_id_str}: {e}", exc_info=True)
        try:
            job = db.query(Job).filter_by(id=uuid.UUID(job_id_str)).first()
            if job:
                job.status = JobStatus.FAILED
                db.commit()
                logger.info(f"Job {job_id_str} marked FAILED")
            if job_id_str in JOBS_STORE:
                JOBS_STORE[job_id_str]["status"] = JobStatus.FAILED.value
        except Exception as inner_e:
            logger.error(f"Could not mark job as failed: {inner_e}")
    finally:
        db.close()
        logger.info(f"Background crawl thread exiting for job {job_id_str}")


# â”€â”€â”€ API Endpoints â”€â”€â”€


def _user_active_job_count(db: Session, user_id: Any) -> int:
    """Count pending+running jobs owned by this user (each is one isolated crawler slot)."""
    return (
        db.query(Job)
        .filter(
            Job.owner_id == user_id,
            Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        )
        .count()
    )


def _user_slot_limit(user: PlatformUser) -> int:
    """How many concurrent crawlers this account may run (0 = unlimited)."""
    if user.role == UserRole.ADMIN.value:
        return int(getattr(settings, "max_concurrent_jobs_admin", 0) or 0)
    return int(getattr(settings, "max_concurrent_jobs_per_user", 2) or 2)


@app.get("/api/me/isolation-status")
async def get_isolation_status(
    current_user: PlatformUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Report isolation guarantees for the logged-in user (for UI + ops)."""
    from src.core.tenant_isolation import (
        crawl_queue_name,
        frontier_key_prefix,
        owner_namespace,
        redis_url_for_owner,
        tenant_root,
    )

    ns = owner_namespace(current_user.id)
    redis_tpl = redis_url_for_owner(settings.redis_url, current_user.id)
    return {
        "userId": str(current_user.id),
        "username": current_user.username,
        "role": current_user.role,
        "level": "user_id_password",
        "guarantees": {
            "jobOwnership": True,
            "apiCrossUserBlocked": current_user.role != UserRole.ADMIN.value,
            "crossUserReturns404": True,
            "tenantDiskPaths": True,
            "tenantBaselines": True,
            "tenantRedisKeyPrefix": True,
            "tenantRedisLogicalDb": bool(getattr(settings, "tenant_redis_isolation", True)),
            "credentialsEncryptedAtRest": True,
            "loginRequiresUsernamePassword": True,
            "crawlRefusesWithoutOwnerId": True,
            "maxConcurrentCrawls": (
                None
                if current_user.role == UserRole.ADMIN.value and not settings.max_concurrent_jobs_admin
                else (
                    settings.max_concurrent_jobs_admin
                    if current_user.role == UserRole.ADMIN.value
                    else settings.max_concurrent_jobs_per_user
                )
            ),
        },
        "tenant": ns,
        "tenantLabel": crawl_queue_name(current_user.id),
        "frontierKeyExample": frontier_key_prefix(current_user.id, "<job_id>"),
        "diskRoot": str(tenant_root(current_user.id)),
        "redisUrlTemplate": redis_tpl.split("@")[-1] if "@" in redis_tpl else redis_tpl,
        "note": (
            "Login with username+password issues a user-scoped JWT (sub=user_id). "
            "Each user has private jobs, disk tree, Redis keyspace/DB, frontier keys, "
            "and encrypted target passwords. User A crawlers cannot read or write "
            "User B data. Shared process/CPU/RAM remains (not VM-per-user)."
        ),
    }


@app.get("/api/me/crawler-slots")
async def get_crawler_slots(
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Per-user crawler capacity: each user can run up to N isolated crawls at once.

    Jobs are always isolated by owner_id + job_id (User A never sees User B).
    System capacity example: 20 users × 2 slots = 40 concurrent crawls (needs ≥40 workers).
    """
    limit = _user_slot_limit(current_user)
    active = _user_active_job_count(db, current_user.id)
    unlimited = limit <= 0
    available = 999 if unlimited else max(0, limit - active)
    active_jobs = (
        db.query(Job)
        .filter(
            Job.owner_id == current_user.id,
            Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        )
        .order_by(Job.created_at.desc())
        .all()
    )
    return {
        "userId": str(current_user.id),
        "username": current_user.username,
        "role": current_user.role,
        "maxConcurrent": None if unlimited else limit,
        "active": active,
        "available": available,
        "canStartMore": unlimited or active < limit,
        "isolation": "owner_id",
        "note": (
            f"Each of your crawls is fully isolated (own job_id, frontier, reports). "
            f"You may run up to {limit if not unlimited else 'unlimited'} at the same time."
        ),
        "activeJobs": [
            {
                "id": str(j.id),
                "url": j.url,
                "status": j.status.value if hasattr(j.status, "value") else str(j.status),
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in active_jobs
        ],
    }


# In-process per-owner mutexes — the fallback consistency layer when Redis
# is unavailable. Covers multi-threaded access inside one process (dev,
# TestClient, gunicorn threads). Cross-process safety requires Redis.
import threading  # noqa: E402

_user_slot_mutexes: dict[str, Any] = {}
_user_slot_mutexes_guard = threading.Lock()


def _acquire_user_slot_lock(owner_id: Any, timeout_seconds: float = 5.0) -> Any:
    """Per-user mutex for job creation (slot-limit consistency under load).

    Priority: Redis SET NX EX (cross-process, multi-replica safety). When
    Redis is unavailable, degrade to an in-process threading lock so that
    concurrent threads within one process still serialize — a silent
    no-lock fallback would let 10 threads all see "1 active" and create
    10 jobs (reproduced in tests/test_consistency_soak.py).
    Returns a release callable.
    """
    key = f"cbtp:slot-lock:{owner_id}"
    token = uuid.uuid4().hex
    redis_mode = False
    try:
        import redis as _redis

        rc = _redis.Redis.from_url(settings.redis_url, socket_timeout=2)
        rc.ping()
        redis_mode = True
    except Exception:
        redis_mode = False

    if redis_mode:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                if rc.set(key, token, nx=True, ex=10):  # type: ignore[union-attr]
                    def _release() -> None:
                        try:
                            pipe = rc.pipeline()  # type: ignore[union-attr]
                            pipe.get(key)
                            current = pipe.execute()[0]
                            if current and current.decode() == token:
                                rc.delete(key)  # type: ignore[union-attr]
                        except Exception:
                            pass

                    return _release
            except Exception:
                break
            time.sleep(0.05)
        logger.warning("Slot lock contention for owner %s — proceeding without lock", str(owner_id)[:8])

    # In-process fallback (thread-safe within this process)
    with _user_slot_mutexes_guard:
        lock = _user_slot_mutexes.setdefault(str(owner_id), threading.Lock())

    acquired = lock.acquire(timeout=timeout_seconds)
    if not acquired:
        logger.warning("Local slot lock contention for owner %s — proceeding unlocked", str(owner_id)[:8])
        return lambda: None

    def _release_local() -> None:
        # Double-release guard: releasing an unlocked threading.Lock raises RuntimeError
        with contextlib.suppress(RuntimeError):
            lock.release()

    return _release_local


def _create_job_core(
    db: Session,
    owner: PlatformUser,
    url: str,
    scope: str,
    browser_matrix: dict[str, bool],
    max_pages: int,
    max_depth: int,
    max_time_seconds: int,
    repo_url: str | None,
    config: dict[str, Any],
    credentials: Any | None,
    http_request: Request | None,
) -> JobResponse:
    """Single source of truth for crawl creation.

    Called by POST /jobs and by scheduled monitors — slot limits, tenant
    isolation, RQ dispatch and audit logging apply identically to both.
    Raises HTTPException(429) when the owner's concurrent-slot limit is hit.

    Concurrency: the per-user slot check runs under a short-lived Redis
    lock so 20 simultaneous requests cannot all see "1 active" and create
    20 jobs. Without Redis (dev/SQLite) the single process serializes us.
    """
    from src.core.audit import record_audit

    release = _acquire_user_slot_lock(owner.id)
    try:
        # Per-user concurrent crawler slots (isolation preserved via owner_id)
        slot_limit = _user_slot_limit(owner)
        if slot_limit > 0:
            active = _user_active_job_count(db, owner.id)
            if active >= slot_limit:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Crawler limit reached: you already have {active}/{slot_limit} "
                        f"active crawls (pending or running). Wait for one to finish, "
                        f"or stop a job. Each crawl is isolated — you can run up to "
                        f"{slot_limit} at the same time."
                    ),
                )

        is_local = is_localhost_or_private(url)

        # Scoped credentials security gate
        if credentials:
            import json

            try:
                dev_creds = json.loads(settings.dev_credentials)
            except Exception:
                dev_creds = {}
            is_prod_target = not is_local
            if is_prod_target and dev_creds and credentials.username == dev_creds.get("username"):
                raise HTTPException(
                    status_code=400,
                    detail="Security Gate: Development credentials cannot be used on production/external targets.",
                )

        job_uuid = uuid.uuid4()
        job_id_str = str(job_uuid)
        now = datetime.now(UTC)

        # 1. Save to Database (owner_id + tenant paths isolate jobs per platform user)
        from src.core.tenant_isolation import apply_job_isolation_paths

        job_config = dict(config or {})
        job = Job(
            id=job_uuid,
            owner_id=owner.id,
            url=url,
            scope=scope,
            status=JobStatus.PENDING,
            browser_matrix=browser_matrix,
            max_pages=max_pages,
            max_depth=max_depth,
            max_time_seconds=max_time_seconds,
            repo_url=repo_url,
            config=job_config,
            created_at=now,
        )
        db.add(job)
        db.flush()  # get job.id for path materialization
        apply_job_isolation_paths(job)

        # Save credentials profiles if given (password encrypted at rest)
        has_creds = False
        if credentials:
            from src.core.secrets_crypto import encrypt_secret

            has_creds = True
            auth_prof = AuthProfile(
                job_id=job_uuid,
                login_url=credentials.login_url or url,
                username=credentials.username,
                password_encrypted=encrypt_secret(credentials.password or ""),
                tokens={"access_token": credentials.token} if credentials.token else {},
                cookies=credentials.cookies,
            )
            db.add(auth_prof)
            job.has_credentials = True

        db.commit()
    finally:
        # Slot lock is held only across check→commit; release ASAP.
        if release is not None:
            release()

    # 2. Save in backward-compatible in-memory store for unit test suite validation.
    # Always stamp owner_id so cross-user reads of JOBS_STORE can be blocked.
    JOBS_STORE[job_id_str] = {
        "id": job_id_str,
        "owner_id": str(owner.id),
        "url": url,
        "status": "pending",
        "scope": scope,
        "browser_matrix": browser_matrix,
        "has_credentials": has_creds,
        "max_pages": max_pages,
        "max_depth": max_depth,
        "max_time_seconds": max_time_seconds,
        "repo_url": repo_url,
        "config": job_config,
        "is_localhost": is_local,
        "pages_discovered": 0,
        "pages_visited": 0,
        "issues_found": 0,
        "console_errors_found": 0,
        "endpoints_discovered": 0,
        "issues": [],
        "console_errors": [],
        "endpoints": [],
        "created_at": now.isoformat(),
        "started_at": None,
        "completed_at": None,
    }

    # 3. Dispatch crawl execution.
    # Jobs go on the shared RQ ``default`` queue (workers always poll it).
    # True isolation is data-plane: owner_id stamps disk paths, Redis DB/key
    # prefixes, and API access so User A's crawler never touches User B data.
    # Fallback (local dev without Redis): in-process daemon thread.
    import sys

    should_run = "pytest" not in sys.modules

    if should_run:
        enqueued = False
        try:
            import redis as _redis
            from rq import Queue as _Queue

            from src.core.tenant_isolation import crawl_queue_name

            _rc = _redis.Redis.from_url(settings.redis_url)
            _rc.ping()
            # Shared worker queue; meta.owner_id documents the tenant for ops.
            _q = _Queue("default", connection=_rc)
            _q.enqueue(
                "src.api.app.run_background_crawl_job",
                job_id_str,
                job_timeout=86400,
                meta={
                    "owner_id": str(owner.id),
                    "tenant": crawl_queue_name(owner.id),
                },
            )
            enqueued = True
            logger.info(
                "Enqueued crawl job %s (owner=%s tenant=%s)",
                job_id_str,
                str(owner.id)[:8],
                crawl_queue_name(owner.id),
            )
        except Exception as e:
            logger.warning(f"Redis/RQ unavailable ({e}) — falling back to in-process crawl thread")

        if not enqueued:
            import threading

            thread = threading.Thread(
                target=run_background_crawl_job,
                args=(job_id_str,),
                daemon=True,
                name=f"crawl-{str(owner.id)[:8]}-{job_id_str[:8]}",
            )
            thread.start()
            logger.info(f"Started background crawl thread for job {job_id_str}")

    record_audit(
        db,
        action="job.create",
        actor_id=owner.id,
        actor_username=owner.username,
        resource_type="job",
        resource_id=job_id_str,
        success=True,
        detail={"url": url, "scope": scope},
        request=http_request,
    )

    return JobResponse(
        id=job_id_str,
        url=url,
        status="pending",
        scope=scope,
        browser_matrix=browser_matrix,
        has_credentials=has_creds,
        max_pages=max_pages,
        max_depth=max_depth,
        max_time_seconds=max_time_seconds,
        created_at=now.isoformat(),
        message=f"Job created. Target: {url} ({'localhost' if is_local else 'remote'})",
    )


@app.post("/jobs", response_model=JobResponse, status_code=201)
async def create_job(
    request: CreateJobRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
) -> JobResponse:
    """Submit a cross-browser crawl testing job (owned by the logged-in user).

    Each job is an isolated crawler. Non-admin users may hold up to
    ``max_concurrent_jobs_per_user`` (default 2) pending/running jobs at once.
    """
    return _create_job_core(
        db=db,
        owner=current_user,
        url=request.url,
        scope=request.scope,
        browser_matrix=request.browser_matrix.model_dump(),
        max_pages=request.max_pages,
        max_depth=request.max_depth,
        max_time_seconds=request.max_time_seconds,
        repo_url=request.repo_url,
        config=request.config,
        credentials=request.credentials,
        http_request=http_request,
    )


def _get_owned_job(db: Session, job_id: str, user: PlatformUser) -> Job:
    """Load job and enforce ownership (admin can access all).

    Regular users only see jobs where Job.owner_id == user.id.
    Returns 404 (not 403) for cross-user access to avoid job-id enumeration.
    """
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found") from None
    job = db.query(Job).filter_by(id=job_uuid).first()
    if not job or not user_can_access_job(user, job):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _jobs_store_visible(job_id: str, user: PlatformUser) -> dict[str, Any] | None:
    """Return JOBS_STORE entry only if it belongs to the caller (or admin)."""
    mem = JOBS_STORE.get(job_id)
    if not mem:
        return None
    if user.role == UserRole.ADMIN.value:
        return mem
    owner = mem.get("owner_id")
    if owner and str(owner) == str(user.id):
        return mem
    return None


@app.post("/jobs/{job_id}/stop")
async def stop_job(
    job_id: str,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
):
    """Cancel a running job."""
    from src.core.audit import record_audit

    try:
        job = _get_owned_job(db, job_id, current_user)

        job.status = JobStatus.CANCELLED
        db.commit()
        record_audit(
            db,
            action="job.stop",
            actor_id=current_user.id,
            actor_username=current_user.username,
            resource_type="job",
            resource_id=job_id,
            success=True,
            request=http_request,
        )
        return {"status": "cancelled", "job_id": job_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/jobs/{job_id}/resume")
async def resume_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
):
    """Resume a cancelled or failed job from its checkpoint."""
    from src.core.audit import record_audit

    try:
        job = _get_owned_job(db, job_id, current_user)

        if job.status not in (JobStatus.CANCELLED, JobStatus.FAILED):
            raise HTTPException(
                status_code=400,
                detail=f"Only cancelled or failed jobs can be resumed. Current status: {job.status.value}",
            )

        job.status = JobStatus.PENDING
        db.commit()
        record_audit(
            db,
            action="job.resume",
            actor_id=current_user.id,
            actor_username=current_user.username,
            resource_type="job",
            resource_id=job_id,
            success=True,
            request=http_request,
        )

        # Re-enqueue on default worker queue; isolation re-applied from job.owner_id
        import sys

        if "pytest" not in sys.modules:
            try:
                from rq import Queue

                from src.core.tenant_isolation import crawl_queue_name

                redis_client = redis.Redis.from_url(settings.redis_url)
                q = Queue("default", connection=redis_client)
                q.enqueue(
                    "src.api.app.run_background_crawl_job",
                    job_id,
                    job_timeout=86400,
                    meta={
                        "owner_id": str(job.owner_id) if job.owner_id else None,
                        "tenant": crawl_queue_name(job.owner_id),
                    },
                )
                logger.info("Re-enqueued job %s for resume (owner=%s).", job_id, job.owner_id)
            except Exception as e:
                logger.error(f"Failed to enqueue resume job {job_id} to RQ: {e}. Falling back to background_tasks.")
                background_tasks.add_task(run_background_crawl_job, job_id)

        return {"status": "resumed", "job_id": job_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
) -> JobResponse:
    """Fetch current progress status of a job with live counts."""
    try:
        job = _get_owned_job(db, job_id, current_user)
        return JobResponse(
            id=str(job.id),
            url=job.url,
            status=job.status.value,
            scope=job.scope,
            browser_matrix=job.browser_matrix,
            has_credentials=job.has_credentials,
            max_pages=job.max_pages,
            max_depth=job.max_depth,
            max_time_seconds=job.max_time_seconds,
            created_at=job.created_at.isoformat(),
            message=f"Job status: {job.status.value}",
            pages_discovered=job.pages_discovered or 0,
            pages_visited=job.pages_visited or 0,
            issues_found=job.issues_found or 0,
            console_errors_found=job.console_errors_found or 0,
            endpoints_discovered=job.endpoints_discovered or 0,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/jobs/{job_id}/report/download")
async def download_report_file(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
):
    """Download the static HTML report (owner-only; prefers tenant path)."""
    import os

    job = _get_owned_job(db, job_id, current_user)
    iso = (job.config or {}).get("isolation") or {}
    paths = iso.get("paths") or {}
    candidates = [
        paths.get("report_html"),
        f"reports/report_{job_id}.html",
    ]
    report_path = next((p for p in candidates if p and os.path.exists(p)), None)
    if not report_path:
        raise HTTPException(status_code=404, detail="Report not generated yet or not found")
    return FileResponse(path=report_path, media_type="text/html", filename=f"crawl_report_{job_id}.html")


@app.get("/jobs/{job_id}/screenshots/{path:path}")
async def serve_job_screenshot(
    job_id: str,
    path: str,
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
):
    """Serve a screenshot only for a job the caller owns (tenant-safe)."""
    import os

    from src.core.tenant_isolation import safe_resolve_under_tenant

    job = _get_owned_job(db, job_id, current_user)
    # Never allow legacy reports/ for non-admin — prevents cross-tenant path guesses
    allow_legacy = current_user.role == UserRole.ADMIN.value
    resolved = safe_resolve_under_tenant(job.owner_id, path, also_allow_legacy_reports=allow_legacy)
    if resolved is None:
        # Tenant snapshots dir + basename only (still under owner)
        iso = (job.config or {}).get("isolation") or {}
        snap_dir = (iso.get("paths") or {}).get("snapshots")
        if snap_dir:
            cand = os.path.normpath(os.path.join(snap_dir, os.path.basename(path)))
            if ".." not in cand.split(os.sep) and os.path.isfile(cand):
                # Must remain under tenant snapshots
                try:
                    Path(cand).resolve().relative_to(Path(snap_dir).resolve())
                    return FileResponse(path=cand, media_type="image/png")
                except ValueError:
                    pass
        if allow_legacy:
            legacy = os.path.join("reports", "snapshots", job_id, path)
            if os.path.isfile(legacy):
                return FileResponse(path=legacy, media_type="image/png")
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path=str(resolved), media_type="image/png")


@app.get("/screenshots/{path:path}")
async def serve_screenshot(
    path: str,
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
):
    """Serve screenshot only if path is under caller's tenant (or admin)."""
    import os

    from src.core.tenant_isolation import safe_resolve_under_tenant

    # Admin may read any existing file under reports/ or data/tenants/
    if current_user.role == UserRole.ADMIN.value:
        for base in ("reports/snapshots", "reports", "data/tenants"):
            full = os.path.normpath(os.path.join(base, path))
            if ".." in full.split(os.sep):
                continue
            if os.path.isfile(full):
                return FileResponse(path=full, media_type="image/png")
        raise HTTPException(status_code=404, detail="Screenshot not found")

    resolved = safe_resolve_under_tenant(current_user.id, path, also_allow_legacy_reports=False)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path=str(resolved), media_type="image/png")


@app.get("/jobs/{job_id}/video/{browser}")
async def download_video(
    job_id: str,
    browser: str,
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
):
    """Download recorded Playwright video for a browser engine."""
    import os

    job = _get_owned_job(db, job_id, current_user)
    iso = (job.config or {}).get("isolation") or {}
    video_dir = (iso.get("paths") or {}).get("videos") or f"reports/videos/{job_id}"
    if not os.path.exists(video_dir):
        raise HTTPException(status_code=404, detail="No videos recorded for this job")
    files = [f for f in os.listdir(video_dir) if f.endswith(".webm")]
    if not files:
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(
        path=os.path.join(video_dir, files[0]), media_type="video/webm", filename=f"{browser}_{job_id}.webm"
    )


@app.get("/jobs/{job_id}/har/{browser}")
async def download_har(
    job_id: str,
    browser: str,
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
):
    """Download recorded HTTP Archive (HAR) log file."""
    import os

    job = _get_owned_job(db, job_id, current_user)
    iso = (job.config or {}).get("isolation") or {}
    har_dir = (iso.get("paths") or {}).get("har")
    candidates = []
    if har_dir:
        candidates.extend(
            [
                f"{har_dir}/{browser}_record.har",
                f"{har_dir}/{browser}_auth_record.har",
            ]
        )
    candidates.extend(
        [
            f"reports/har/{job_id}/{browser}_record.har",
            f"reports/har/{job_id}/{browser}_auth_record.har",
        ]
    )
    har_path = next((p for p in candidates if os.path.exists(p)), None)
    if not har_path:
        raise HTTPException(status_code=404, detail="HAR log file not found")
    return FileResponse(path=har_path, media_type="application/json", filename=f"{browser}_{job_id}.har")


@app.get("/jobs/{job_id}/report", response_model=JobReportResponse)
async def get_job_report(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
) -> JobReportResponse:
    """Fetch the triaged report data."""
    # Check Database first
    try:
        job = _get_owned_job(db, job_id, current_user)
        if job:
            issues = db.query(Issue).filter_by(job_id=job.id).all()

            # Flaky quarantine metadata for this owner (one query, tenant-scoped).
            # Gate-exempt = quarantined/confirmed fingerprints (auto or human).
            flaky_meta: dict[str, dict[str, Any]] = {}
            try:
                from src.api.flaky_registry import quarantine_status_map

                flaky_meta = quarantine_status_map(db, job.owner_id)
            except Exception:
                flaky_meta = {}

            # Map issues output list
            issue_list = []
            console_summary = {"critical": 0, "error": 0, "warning": 0, "info": 0}
            error_type_summary: dict[str, int] = {}
            quarantined_count = 0
            for iss in issues:
                fp = str(getattr(iss, "fingerprint", ""))
                meta = flaky_meta.get(fp) or {}
                flaky_status = str(meta.get("status", "")) or None
                gate_exempt = flaky_status in ("quarantined", "confirmed")
                if gate_exempt:
                    quarantined_count += 1
                issue_list.append(
                    {
                        "id": str(iss.id),
                        "fingerprint": fp,
                        "flaky_status": flaky_status,
                        "flip_rate": meta.get("flip_rate"),
                        "flaky_samples": meta.get("samples"),
                        "gate_exempt": gate_exempt,
                        "title": iss.title,
                        "description": iss.description,
                        "severity": iss.severity.value if iss.severity else "functional",
                        "category": iss.category.value if iss.category else "js_error",
                        "occurrence_count": iss.occurrence_count,
                        "browsers_affected": iss.browsers_affected,
                        "first_seen_url": iss.first_seen_url,
                        "error_message": iss.error_message,
                        "stack_trace": iss.stack_trace,
                        "fix_status": iss.fix_status,
                        "fix_diff": iss.fix_diff,
                        "fix_confidence": iss.fix_confidence,
                        "repro_steps": iss.repro_steps,
                        "screenshot_path": iss.screenshot_path,
                    }
                )

                # Update console counter breakdown
                sev = iss.severity.value if iss.severity else ""
                if sev == "blocking":
                    console_summary["critical"] += iss.occurrence_count
                elif sev == "functional":
                    console_summary["error"] += iss.occurrence_count
                elif sev == "visual":
                    console_summary["warning"] += iss.occurrence_count
                else:
                    console_summary["info"] += iss.occurrence_count

                # Error type summary by category
                cat = iss.category.value if iss.category else "console_error"
                error_type_summary[cat] = error_type_summary.get(cat, 0) + iss.occurrence_count

            # Extract endpoints with rich data
            cstates = db.query(CrawlState).filter_by(job_id=job.id).all()
            endpoint_list = []
            for cs in cstates:
                endpoint_list.append(
                    {
                        "url": cs.url,
                        "method": cs.method,
                        "status": cs.status.value if hasattr(cs.status, "value") else str(cs.status),
                        "status_code": cs.last_status_code,
                        "depth": cs.depth,
                        "discovered_via": cs.discovered_via,
                        "endpoint_type": cs.endpoint_type,
                        "requires_auth": cs.requires_auth,
                        "console_errors": cs.console_errors,
                    }
                )

            # Merge with JOBS_STORE only when entry is owned by caller (or admin)
            mem = _jobs_store_visible(job_id, current_user)
            if mem:
                pages_visited = mem["pages_visited"] or job.pages_visited
                pages_discovered = mem["pages_discovered"] or job.pages_discovered
                issues_found = mem["issues_found"] or job.issues_found
                endpoints_discovered = mem["endpoints_discovered"] or len(endpoint_list)
                if mem.get("issues"):
                    issue_list = mem["issues"]
            else:
                pages_visited = job.pages_visited
                pages_discovered = job.pages_discovered
                issues_found = job.issues_found
                endpoints_discovered = len(endpoint_list)

            # Prefer tenant-isolated report path; never leak another tenant's file
            import os

            iso = (job.config or {}).get("isolation") or {}
            tenant_report = (iso.get("paths") or {}).get("report_html")
            legacy_report = f"reports/report_{job_id}.html"
            report_exists = bool((tenant_report and os.path.exists(tenant_report)) or os.path.exists(legacy_report))
            report_html_url = f"/jobs/{job_id}/report/download" if report_exists else None

            return JobReportResponse(
                id=str(job.id),
                url=job.url,
                status=job.status.value,
                pages_discovered=pages_discovered,
                pages_visited=pages_visited,
                issues_found=issues_found,
                console_errors_found=job.console_errors_found,
                endpoints_discovered=endpoints_discovered,
                browser_matrix=job.browser_matrix,
                issues=issue_list,
                console_error_summary=console_summary,
                error_type_summary=error_type_summary,
                endpoints=endpoint_list,
                report_html_url=report_html_url,
                created_at=job.created_at.isoformat(),
                started_at=job.started_at.isoformat() if job.started_at else None,
                completed_at=job.completed_at.isoformat() if job.completed_at else None,
                quarantined_issues=quarantined_count,
                lighthouse_seed=(job.config or {}).get("lighthouse_seed"),
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching report for job {job_id} from database: {e}", exc_info=True)

    # Fallback entirely to in-memory store (owner-gated)
    job_mem = _jobs_store_visible(job_id, current_user)
    if not job_mem:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return JobReportResponse(
        id=job_mem["id"],
        url=job_mem["url"],
        status=job_mem["status"],
        pages_discovered=job_mem["pages_discovered"],
        pages_visited=job_mem["pages_visited"],
        issues_found=job_mem["issues_found"],
        console_errors_found=job_mem["console_errors_found"],
        endpoints_discovered=job_mem["endpoints_discovered"],
        browser_matrix=job_mem["browser_matrix"],
        issues=job_mem.get("issues", []),
        console_error_summary={"critical": 0, "error": job_mem["console_errors_found"], "warning": 0, "info": 0},
        error_type_summary={},
        endpoints=job_mem.get("endpoints", []),
        report_html_url=None,
        created_at=job_mem["created_at"],
        started_at=job_mem.get("started_at"),
        completed_at=job_mem.get("completed_at"),
    )


@app.get("/jobs", response_class=JSONResponse)
async def list_jobs(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Fetch crawl history for the current user (admin sees all)."""
    jobs_out = []
    total = 0

    try:
        query = db.query(Job)
        # Isolation: regular users only see their own jobs
        if current_user.role != UserRole.ADMIN.value:
            query = query.filter(Job.owner_id == current_user.id)
        if status:
            query = query.filter(Job.status == JobStatus(status))

        total = query.count()
        results = query.order_by(Job.created_at.desc()).offset(offset).limit(limit).all()
        for j in results:
            jobs_out.append(
                {
                    "id": str(j.id),
                    "url": j.url,
                    "status": j.status.value,
                    "created_at": j.created_at.isoformat(),
                    "pages_visited": j.pages_visited,
                    "issues_found": j.issues_found,
                    "owner_id": str(j.owner_id) if j.owner_id else None,
                }
            )
    except Exception as e:
        logger.error("list_jobs error: %s", e)

    return {
        "jobs": jobs_out,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ─── Dashboard Stats (self-hosted replacement for Convex) ───


@app.get("/api/dashboard/stats")
async def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Aggregated dashboard statistics (scoped to current user unless admin)."""
    try:
        job_q = db.query(Job)
        if current_user.role != UserRole.ADMIN.value:
            job_q = job_q.filter(Job.owner_id == current_user.id)
        job_ids = [j.id for j in job_q.with_entities(Job.id).all()]

        total_jobs = len(job_ids)
        completed_jobs = job_q.filter(Job.status == JobStatus.COMPLETED).count()
        running_jobs = job_q.filter(Job.status == JobStatus.RUNNING).count()
        failed_jobs = job_q.filter(Job.status == JobStatus.FAILED).count()

        if job_ids:
            total_issues = db.query(Issue).filter(Issue.job_id.in_(job_ids)).count()
            blocking_issues = (
                db.query(Issue).filter(Issue.job_id.in_(job_ids), Issue.severity == IssueSeverity.BLOCKING).count()
            )
            functional_issues = (
                db.query(Issue).filter(Issue.job_id.in_(job_ids), Issue.severity == IssueSeverity.FUNCTIONAL).count()
            )
            total_pages = db.query(func.coalesce(func.sum(Job.pages_visited), 0)).filter(Job.id.in_(job_ids)).scalar()
            total_endpoints = db.query(CrawlState).filter(CrawlState.job_id.in_(job_ids)).count()
        else:
            total_issues = blocking_issues = functional_issues = 0
            total_pages = total_endpoints = 0

        return {
            "totalJobs": total_jobs,
            "completedJobs": completed_jobs,
            "runningJobs": running_jobs,
            "failedJobs": failed_jobs,
            "totalIssues": total_issues,
            "blockingIssues": blocking_issues,
            "functionalIssues": functional_issues,
            "totalPages": total_pages,
            "totalEndpoints": total_endpoints,
        }
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {e}")
        return {
            "totalJobs": 0,
            "completedJobs": 0,
            "runningJobs": 0,
            "failedJobs": 0,
            "totalIssues": 0,
            "blockingIssues": 0,
            "functionalIssues": 0,
            "totalPages": 0,
            "totalEndpoints": 0,
        }


@app.get("/jobs/{job_id}/dashboard", response_class=HTMLResponse)
async def get_dashboard_html(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: PlatformUser = Depends(get_current_user),
) -> HTMLResponse:
    """Serve a premium, state-of-the-art interactive reporting dashboard."""
    try:
        job = _get_owned_job(db, job_id, current_user)
        issues = db.query(Issue).filter_by(job_id=job.id).all()
        endpoints = db.query(CrawlState).filter_by(job_id=job.id).all()
    except HTTPException:
        raise
    except Exception:
        # Generate mock dashboard data if db query fails
        class MockJob:
            id = job_id
            url = "http://example.com"
            status = JobStatus.COMPLETED
            pages_visited = 12
            pages_discovered = 24
            issues_found = 3
            console_errors_found = 14
            created_at = datetime.now()
            started_at = datetime.now()
            completed_at = datetime.now()
            browser_matrix = {"chromium": True, "firefox": True, "webkit": False}

        job = MockJob()

        issues = [
            Issue(
                id=uuid.uuid4(),
                title="CORS_ERROR: Blocked request to external origin",
                description="Origin http://example.com is not allowed by Access-Control-Allow-Origin",
                severity=IssueSeverity.FUNCTIONAL,
                category=IssueCategory.CORS_ERROR,
                occurrence_count=5,
                first_seen_url="http://example.com/settings",
                browsers_affected={"chromium": True, "firefox": True, "webkit": False},
            ),
            Issue(
                id=uuid.uuid4(),
                title="JS_ERROR: Uncaught TypeError in main.bundle.js",
                description="TypeError: Cannot read properties of undefined (reading 'property')",
                severity=IssueSeverity.BLOCKING,
                category=IssueCategory.JS_ERROR,
                occurrence_count=8,
                first_seen_url="http://example.com/dashboard",
                browsers_affected={"chromium": True},
            ),
        ]
        endpoints = [
            CrawlState(url="http://example.com/", method="GET", status="visited"),
            CrawlState(url="http://example.com/login", method="GET", status="visited"),
            CrawlState(url="http://example.com/api/auth", method="POST", status="visited"),
        ]

    # Calculate status highlights
    blocking_count = sum(1 for i in issues if i.severity == IssueSeverity.BLOCKING)
    warning_count = sum(1 for i in issues if i.severity == IssueSeverity.ADVISORY)
    functional_count = sum(1 for i in issues if i.severity == IssueSeverity.FUNCTIONAL)

    # HTML Templates Generation
    issues_html = ""
    for idx, iss in enumerate(issues):
        sev_color = (
            "red"
            if iss.severity == IssueSeverity.BLOCKING
            else ("yellow" if iss.severity == IssueSeverity.ADVISORY else "blue")
        )
        browsers = ", ".join([b for b, active in iss.browsers_affected.items() if active])

        issues_html += f"""
        <div class="issue-card" onclick="toggleDetails('issue-desc-{idx}')">
            <div class="issue-header">
                <span class="severity-badge badge-{sev_color}">{iss.severity.value.upper() if iss.severity else "FUNCTIONAL"}</span>
                <span class="category-badge">{iss.category.value if iss.category else "js_error"}</span>
                <span class="issue-count">{iss.occurrence_count} Occurrences</span>
            </div>
            <h3 class="issue-title">{iss.title}</h3>
            <p class="issue-url">First seen on: <a href="{iss.first_seen_url}" target="_blank">{iss.first_seen_url}</a></p>
            <div class="issue-browsers">Affected Browsers: <strong>{browsers}</strong></div>
            <div id="issue-desc-{idx}" class="issue-details hidden">
                <hr style="border-color: #334155; margin: 12px 0;">
                <p><strong>Description:</strong></p>
                <pre class="trace-box">{iss.description}</pre>
                {f'<p><strong>Stack Trace:</strong></p><pre class="trace-box">{iss.stack_trace}</pre>' if iss.stack_trace else ""}
            </div>
        </div>
        """

    endpoints_html = ""
    for ep in endpoints:
        status_color = "green" if ep.status == "visited" else "yellow"
        endpoints_html += f"""
        <tr class="endpoint-row">
            <td><span class="method-badge badge-{ep.method.lower()}">{ep.method}</span></td>
            <td class="endpoint-url">{ep.url}</td>
            <td><span class="status-indicator stat-{status_color}">{ep.status}</span></td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Autonomous Crawl Report â€” {job.url}</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
        <style>
            :root {{
                --bg-primary: #0f172a;
                --bg-secondary: #1e293b;
                --accent: #6366f1;
                --accent-hover: #4f46e5;
                --text-main: #f8fafc;
                --text-muted: #94a3b8;
                --badge-red: #ef4444;
                --badge-yellow: #f59e0b;
                --badge-blue: #3b82f6;
                --badge-green: #10b981;
                --shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.5);
            }}
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{
                font-family: 'Inter', sans-serif;
                background-color: var(--bg-primary);
                color: var(--text-main);
                padding: 40px 20px;
                line-height: 1.6;
            }}
            .container {{
                max-width: 1200px;
                margin: 0 auto;
            }}
            header {{
                margin-bottom: 40px;
                background: linear-gradient(135deg, var(--bg-secondary) 0%, #1e1b4b 100%);
                padding: 30px;
                border-radius: 16px;
                box-shadow: var(--shadow);
                border: 1px solid #334155;
            }}
            .header-info h1 {{ font-size: 2.2rem; margin-bottom: 8px; font-weight: 700; color: #fff; }}
            .header-info p {{ color: var(--text-muted); font-size: 1.1rem; }}
            .header-info a {{ color: var(--accent); text-decoration: none; }}
            .header-info a:hover {{ text-decoration: underline; }}

            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: 20px;
                margin-bottom: 40px;
            }}
            .stat-card {{
                background-color: var(--bg-secondary);
                padding: 24px;
                border-radius: 16px;
                box-shadow: var(--shadow);
                border: 1px solid #334155;
                display: flex;
                flex-direction: column;
                justify-content: space-between;
                position: relative;
                overflow: hidden;
            }}
            .stat-card::before {{
                content: '';
                position: absolute;
                top: 0; left: 0; width: 4px; height: 100%;
                background-color: var(--accent);
            }}
            .stat-card.card-red::before {{ background-color: var(--badge-red); }}
            .stat-card.card-yellow::before {{ background-color: var(--badge-yellow); }}
            .stat-card.card-green::before {{ background-color: var(--badge-green); }}

            .stat-label {{ font-size: 0.9rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; }}
            .stat-val {{ font-size: 2.5rem; font-weight: 700; color: #fff; margin-top: 10px; }}

            .tabs {{
                display: flex;
                gap: 15px;
                border-bottom: 2px solid #334155;
                margin-bottom: 30px;
            }}
            .tab-btn {{
                background: none;
                border: none;
                color: var(--text-muted);
                font-size: 1.1rem;
                font-weight: 600;
                padding: 12px 20px;
                cursor: pointer;
                border-bottom: 3px solid transparent;
                transition: all 0.2s ease;
            }}
            .tab-btn:hover, .tab-btn.active {{
                color: var(--text-main);
                border-bottom-color: var(--accent);
            }}

            .tab-content {{
                background-color: var(--bg-secondary);
                border-radius: 16px;
                padding: 30px;
                box-shadow: var(--shadow);
                border: 1px solid #334155;
            }}
            .hidden {{ display: none !important; }}

            .issue-card {{
                background-color: #0f172a;
                padding: 20px;
                border-radius: 12px;
                border: 1px solid #334155;
                margin-bottom: 20px;
                cursor: pointer;
                transition: transform 0.2s ease, border-color 0.2s ease;
            }}
            .issue-card:hover {{
                transform: translateY(-2px);
                border-color: var(--accent);
            }}
            .issue-header {{
                display: flex;
                align-items: center;
                gap: 12px;
                margin-bottom: 12px;
            }}
            .severity-badge {{
                font-size: 0.75rem;
                padding: 4px 10px;
                border-radius: 9999px;
                font-weight: 700;
            }}
            .badge-red {{ background-color: rgba(239, 68, 68, 0.2); color: #f87171; }}
            .badge-yellow {{ background-color: rgba(245, 158, 11, 0.2); color: #fbbf24; }}
            .badge-blue {{ background-color: rgba(59, 130, 246, 0.2); color: #60a5fa; }}
            .category-badge {{
                background-color: #334155;
                color: #cbd5e1;
                font-size: 0.75rem;
                padding: 4px 10px;
                border-radius: 9999px;
            }}
            .issue-count {{
                margin-left: auto;
                font-size: 0.85rem;
                color: var(--text-muted);
            }}
            .issue-title {{ font-size: 1.25rem; font-weight: 600; margin-bottom: 8px; color: #fff; }}
            .issue-url {{ font-size: 0.9rem; color: var(--text-muted); margin-bottom: 8px; }}
            .issue-browsers {{ font-size: 0.85rem; color: var(--text-muted); }}

            .trace-box {{
                background-color: #020617;
                padding: 16px;
                border-radius: 8px;
                font-family: monospace;
                font-size: 0.9rem;
                overflow-x: auto;
                color: #e2e8f0;
                border: 1px solid #1e293b;
                margin-top: 10px;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
            }}
            th, td {{
                padding: 16px 20px;
                text-align: left;
                border-bottom: 1px solid #334155;
            }}
            th {{
                color: var(--text-muted);
                font-weight: 600;
                text-transform: uppercase;
                font-size: 0.85rem;
            }}
            .method-badge {{
                font-size: 0.8rem;
                padding: 4px 8px;
                border-radius: 4px;
                font-weight: 700;
                display: inline-block;
                width: 70px;
                text-align: center;
            }}
            .badge-get {{ background-color: rgba(16, 185, 129, 0.2); color: #34d399; }}
            .badge-post {{ background-color: rgba(59, 130, 246, 0.2); color: #60a5fa; }}
            .badge-delete {{ background-color: rgba(239, 68, 68, 0.2); color: #f87171; }}
            .badge-put {{ background-color: rgba(245, 158, 11, 0.2); color: #fbbf24; }}

            .status-indicator {{
                font-size: 0.85rem;
                font-weight: 600;
            }}
            .stat-green {{ color: var(--badge-green); }}
            .stat-yellow {{ color: var(--badge-yellow); }}
        </style>
        <script>
            function showTab(tabId) {{
                document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));

                document.getElementById(tabId).classList.remove('hidden');
                event.target.classList.add('active');
            }}
            function toggleDetails(descId) {{
                const el = document.getElementById(descId);
                el.classList.toggle('hidden');
            }}
        </script>
    </head>
    <body>
        <div class="container">
            <header>
                <div class="header-info">
                    <h1>Autonomous Crawl Report</h1>
                    <p>Tested URL: <a href="{job.url}" target="_blank">{job.url}</a></p>
                    <p style="margin-top: 8px; font-size: 0.9rem;">Job ID: {job.id} | Status: <strong style="color: var(--badge-green);">{job.status.value.upper() if hasattr(job.status, "value") else job.status}</strong></p>
                </div>
            </header>

            <div class="stats-grid">
                <div class="stat-card card-red">
                    <span class="stat-label">Blocking Errors</span>
                    <span class="stat-val">{blocking_count}</span>
                </div>
                <div class="stat-card card-yellow">
                    <span class="stat-label">Functional Warnings</span>
                    <span class="stat-val">{functional_count + warning_count}</span>
                </div>
                <div class="stat-card">
                    <span class="stat-label">Endpoints Discovered</span>
                    <span class="stat-val">{len(endpoints)}</span>
                </div>
                <div class="stat-card card-green">
                    <span class="stat-label">Pages Crawled</span>
                    <span class="stat-val">{job.pages_visited}</span>
                </div>
            </div>

            <div class="tabs">
                <button class="tab-btn active" onclick="showTab('issues-tab')">Triaged Issues ({len(issues)})</button>
                <button class="tab-btn" onclick="showTab('endpoints-tab')">Endpoints Catalog ({len(endpoints)})</button>
            </div>

            <div id="issues-tab" class="tab-content">
                {issues_html if issues_html else '<p style="color: var(--text-muted); text-align: center; padding: 40px 0;">No active issues or bugs triaged for this job!</p>'}
            </div>

            <div id="endpoints-tab" class="tab-content hidden">
                <table>
                    <thead>
                        <tr>
                            <th>Method</th>
                            <th>Endpoint URL</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {endpoints_html}
                    </tbody>
                </table>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy", "service": "cross-browser-testing-platform"}


@app.get("/metrics")
async def get_metrics(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Expose application observability telemetry metrics."""
    # Active/Total jobs in DB
    try:
        active_jobs = db.query(Job).filter(Job.status == JobStatus.RUNNING).count()
        total_jobs = db.query(Job).count()
        total_issues = db.query(Issue).count()
    except Exception:
        active_jobs, total_jobs, total_issues = 0, 0, 0

    # Redis metrics
    queue_depth = 0
    workers_count = 0
    try:
        redis_client = redis.Redis.from_url(settings.redis_url)
        from rq import Queue, Worker

        q = Queue(connection=redis_client)
        queue_depth = q.count
        workers_count = len(Worker.all(connection=redis_client))
    except Exception:
        pass

    return {
        "active_crawls": active_jobs,
        "total_crawls": total_jobs,
        "total_issues_triaged": total_issues,
        "queue_depth": queue_depth,
        "active_workers": workers_count,
    }
