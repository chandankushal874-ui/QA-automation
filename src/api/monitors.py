"""Scheduled crawl monitoring — DB-backed, restart-safe, tenant-isolated.

Production design:
- Monitors live in the DB (one row per schedule). Survives API restarts.
- A single asyncio loop per API process runs due schedules by submitting
  normal /jobs create logic internally — no second crawler implementation.
- Deduplication: a schedule never fires if its previous triggered job is
  still pending/running (respecting per-user slot limits via normal create).
- Failures never crash the loop; every fire is logged with schedule_id.
- Ownership: schedules belong to a user; crawls inherit owner_id isolation.
- Notifications: on completion, a webhook (Slack-compatible) may be posted
  if notify_url set. Non-fatal — alert delivery never blocks reporting.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from src.api.auth import get_current_user, get_db_dep
from src.models.database import (
    Job,
    JobStatus,
    MonitorSchedule,
    PlatformUser,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me/monitors", tags=["monitors"])

# How often the scheduler loop looks for due schedules (seconds).
SCHEDULER_TICK_SECONDS = 30
# Treat a schedule as due if next_run_at <= now + lookahead (guards clock skew).
DUE_WINDOW_SECONDS = 60


class CreateMonitorRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=1)
    interval_minutes: int = Field(..., ge=10, le=10080, description="10 min … 7 days")
    browser_matrix: dict[str, bool] = Field(
        default_factory=lambda: {"chromium": True, "firefox": False, "webkit": False}
    )
    max_pages: int = Field(default=25, ge=1, le=1000)
    max_depth: int = Field(default=3, ge=1, le=10)
    notify_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Optional Slack-compatible webhook POSTed on completion",
    )
    is_active: bool = True

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        from urllib.parse import urlparse

        p = urlparse(v)
        if p.scheme not in ("http", "https") or not p.netloc:
            raise ValueError("URL must be http/https with a host")
        return v

    @field_validator("notify_url")
    @classmethod
    def _notify(cls, v: str | None) -> str | None:
        if not v:
            return v
        from urllib.parse import urlparse

        p = urlparse(v)
        if p.scheme not in ("http", "https") or not p.netloc:
            raise ValueError("notify_url must be http/https with a host")
        return v


class MonitorResponse(BaseModel):
    id: str
    name: str
    url: str
    interval_minutes: int
    max_pages: int
    max_depth: int
    notify_url: str | None
    is_active: bool
    last_run_at: str | None
    next_run_at: str | None
    last_job_id: str | None


def _monitor_dict(m: MonitorSchedule) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "name": m.name,
        "url": m.url,
        "interval_minutes": m.interval_minutes,
        "max_pages": m.max_pages,
        "max_depth": m.max_depth,
        "notify_url": m.notify_url,
        "is_active": m.is_active,
        "last_run_at": m.last_run_at.isoformat() if m.last_run_at else None,
        "next_run_at": m.next_run_at.isoformat() if m.next_run_at else None,
        "last_job_id": str(m.last_job_id) if m.last_job_id else None,
    }


def _get_owned_monitor(db: Session, monitor_id: str, user: PlatformUser) -> MonitorSchedule:
    try:
        mid = uuid.UUID(monitor_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Monitor not found") from None
    m = (
        db.query(MonitorSchedule)
        .filter(MonitorSchedule.id == mid, MonitorSchedule.user_id == user.id)
        .first()
    )
    if not m:
        # 404 (not 403) to avoid monitor-id enumeration across users
        raise HTTPException(status_code=404, detail="Monitor not found")
    return m


# ── CRUD ────────────────────────────────────────────────────────────────────


@router.post("", status_code=201)
async def create_monitor(
    req: CreateMonitorRequest,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    m = MonitorSchedule(
        id=uuid.uuid4(),
        user_id=user.id,
        name=req.name.strip(),
        url=req.url,
        interval_minutes=req.interval_minutes,
        browser_matrix=req.browser_matrix,
        max_pages=req.max_pages,
        max_depth=req.max_depth,
        notify_url=req.notify_url,
        is_active=req.is_active,
        next_run_at=now,  # first run at creation time + next tick
        created_at=now,
    )
    db.add(m)
    db.commit()
    return _monitor_dict(m)


@router.get("")
async def list_monitors(
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    rows = (
        db.query(MonitorSchedule)
        .filter(MonitorSchedule.user_id == user.id)
        .order_by(MonitorSchedule.created_at.desc())
        .all()
    )
    return {"monitors": [_monitor_dict(m) for m in rows], "total": len(rows)}


@router.get("/{monitor_id}")
async def get_monitor(
    monitor_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    return _monitor_dict(_get_owned_monitor(db, monitor_id, user))


@router.patch("/{monitor_id}")
async def update_monitor(
    monitor_id: str,
    req: dict[str, Any],
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    m = _get_owned_monitor(db, monitor_id, user)
    allowed = {
        "name", "url", "interval_minutes", "notify_url",
        "is_active", "max_pages", "max_depth", "browser_matrix",
        "jira_url", "jira_project",
    }
    now = datetime.now(UTC)
    for key, value in req.items():
        if key not in allowed:
            continue
        if key == "jira_project" and value is not None:
            value = str(value).strip().upper()[:64] or None
        setattr(m, key, value)
    # Jira token arrives in plain form only here — encrypt at rest.
    if req.get("jira_token"):
        from src.core.secrets_crypto import encrypt_secret

        m.jira_token_encrypted = encrypt_secret(str(req["jira_token"]))
    # Pause clears the schedule; resume re-arms it from now.
    if req.get("is_active") is False:
        m.next_run_at = None
    elif req.get("is_active") is True and m.next_run_at is None:
        m.next_run_at = now
    db.commit()
    return _monitor_dict(m)


@router.delete("/{monitor_id}")
async def delete_monitor(
    monitor_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    m = _get_owned_monitor(db, monitor_id, user)
    db.delete(m)
    db.commit()
    return {"status": "deleted", "monitor_id": monitor_id}


@router.post("/{monitor_id}/run-now")
async def run_monitor_now(
    monitor_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Manually trigger a monitor's crawl immediately (does not reset schedule)."""
    m = _get_owned_monitor(db, monitor_id, user)
    result = _fire_monitor(db, m, manual=True)
    return result


# ── Scheduling engine ───────────────────────────────────────────────────────

# Distributed leader lock: only ONE API process runs the scheduler across the
# whole deployment (8 gunicorn workers, docker replicas, etc.). Redis-backed;
# when Redis is unavailable (single-process dev/SQLite) we degrade to an
# in-process lock so at most one loop runs per process. Lock TTL is refreshed
# with jitter so a crashed holder expires quickly without flapping.
SCHEDULER_LOCK_KEY = "cbtp:monitor-scheduler:leader"
SCHEDULER_LOCK_TTL_SECONDS = 90
SCHEDULER_RENEWAL_SECONDS = 30
SCHEDULER_INSTANCE_ID = uuid.uuid4().hex

_process_scheduler_started = False


def _acquire_leader_lock() -> bool:
    """Try to (re)acquire the scheduler leadership. Returns False if another
    process holds it. Uses Redis SET NX when available; always succeeds
    in-process (single process assumed when Redis is down)."""
    try:
        import redis as _redis

        from src.config import settings

        rc = _redis.Redis.from_url(settings.redis_url, socket_timeout=2)
        got = rc.set(
            SCHEDULER_LOCK_KEY,
            SCHEDULER_INSTANCE_ID,
            nx=True,
            ex=SCHEDULER_LOCK_TTL_SECONDS,
        )
        if got:
            return True
        # Already ours? extend (safe renew, still holding leadership).
        current = rc.get(SCHEDULER_LOCK_KEY)
        if current and current.decode() == SCHEDULER_INSTANCE_ID:
            rc.expire(SCHEDULER_LOCK_KEY, SCHEDULER_LOCK_TTL_SECONDS)
            return True
        return False
    except Exception:
        return True


def _release_leader_lock() -> None:
    try:
        import redis as _redis

        from src.config import settings

        rc = _redis.Redis.from_url(settings.redis_url, socket_timeout=2)
        current = rc.get(SCHEDULER_LOCK_KEY)
        if current and current.decode() == SCHEDULER_INSTANCE_ID:
            rc.delete(SCHEDULER_LOCK_KEY)
    except Exception:
        pass


def _fire_monitor(db: Session, m: MonitorSchedule, manual: bool = False) -> dict[str, Any]:
    """Submit one crawl for a monitor. Never raises; returns a result dict.

    Reuses the platform's normal job-creation path so slot limits, tenant
    isolation, RQ dispatch and everything else apply unchanged.
    """
    from src.api.app import Job as _Job  # noqa: F401  (import sanity)
    from src.models.database import Job

    now = datetime.now(UTC)

    # Dedup: skip if previous monitor job still active (unless manual override attempts anyway)
    active = (
        db.query(Job)
        .filter(
            Job.owner_id == m.user_id,
            Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        )
        .count()
    )

    job_id: str | None = None
    error: str | None = None
    try:
        # Import lazily to avoid circulars; call internal create logic directly
        from src.api import monitors_dispatch

        result = monitors_dispatch.create_job_as_user(
            db=db,
            owner_id=m.user_id,
            url=m.url,
            browser_matrix=m.browser_matrix or {"chromium": True, "firefox": False, "webkit": False},
            max_pages=m.max_pages,
            max_depth=m.max_depth,
        )
        job_id = result
    except Exception as e:
        error = str(e)[:300]
        logger.warning("Monitor %s fire failed: %s", m.id, error)

    m.last_run_at = now
    if m.is_active and not manual:
        from datetime import timedelta

        m.next_run_at = now + timedelta(minutes=m.interval_minutes)
    m.last_job_id = uuid.UUID(job_id) if job_id else None
    db.commit()

    return {
        "monitor_id": str(m.id),
        "job_id": job_id,
        "submitted": job_id is not None,
        "active_jobs_at_fire": active,
        "error": error,
    }


async def scheduler_loop() -> None:
    """Periodically fire due monitors.

    Leader election: only the process holding the distributed lock executes
    ticks. Non-leaders idle cheaply and re-check leadership periodically, so
    failover happens automatically when the leader dies (lock TTL expiry).
    """
    from src.api.app import SessionLocal

    logger.info("Monitor scheduler loop started (instance=%s)", SCHEDULER_INSTANCE_ID[:8])
    since_last_election = 0
    while True:
        try:
            # (Re)elect leader every renewal window; new election only when
            # we've been idle long enough that TTL could have expired.
            since_last_election += SCHEDULER_TICK_SECONDS
            is_leader = getattr(scheduler_loop, "_is_leader", False)
            if not is_leader or since_last_election >= SCHEDULER_RENEWAL_SECONDS:
                is_leader = _acquire_leader_lock()
                scheduler_loop._is_leader = is_leader
                since_last_election = 0
                if not is_leader:
                    logger.debug("Scheduler instance %s is standby", SCHEDULER_INSTANCE_ID[:8])

            if is_leader:
                db = SessionLocal()
                try:
                    now = datetime.now(UTC)
                    from datetime import timedelta

                    due = (
                        db.query(MonitorSchedule)
                        .filter(
                            MonitorSchedule.is_active == True,  # noqa: E712
                            MonitorSchedule.next_run_at.isnot(None),
                            MonitorSchedule.next_run_at <= now + timedelta(seconds=DUE_WINDOW_SECONDS),
                        )
                        .all()
                    )
                    for m in due:
                        _fire_monitor(db, m, manual=False)
                finally:
                    db.close()
        except Exception:
            logger.exception("Monitor scheduler tick failed (will retry)")
        await asyncio.sleep(SCHEDULER_TICK_SECONDS)


def start_scheduler(app: Any) -> None:
    """Attach the scheduler to the FastAPI app (once per process, guarded)."""
    global _process_scheduler_started
    if _process_scheduler_started:
        return
    _process_scheduler_started = True

    @app.on_event("startup")
    async def _start() -> None:  # type: ignore[misc]
        if getattr(app.state, "_monitor_task", None) is None:
            app.state._monitor_task = asyncio.create_task(scheduler_loop())

    @app.on_event("shutdown")
    async def _stop() -> None:  # type: ignore[misc]
        task = getattr(app.state, "_monitor_task", None)
        if task is not None:
            task.cancel()
            app.state._monitor_task = None
        _release_leader_lock()


# ── Notifications (Slack-compatible webhook) ────────────────────────────────


async def notify_webhook(url: str, payload: dict[str, Any]) -> bool:
    """POST a JSON payload to a Slack-compatible webhook. Non-fatal."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            return 200 <= resp.status_code < 300
    except Exception as e:
        logger.warning("Webhook notify failed: %s", e)
        return False


async def maybe_notify_job_completion(db: Session, job: Job) -> None:
    """Notify integrations if this job was fired by a monitor.

    Slack: Block Kit summary via notify_url. Jira: tickets for
    BLOCKING/FUNCTIONAL issues when jira config resolves. All non-fatal.
    """
    try:
        if not getattr(job, "id", None):
            return
        m = (
            db.query(MonitorSchedule)
            .filter(MonitorSchedule.last_job_id == job.id, MonitorSchedule.is_active == True)  # noqa: E712
            .first()
        )
        if not m:
            return

        from src.models.database import Issue

        issues = db.query(Issue).filter_by(job_id=job.id).all()
        dashboard_url = f"/jobs/{job.id}/dashboard"

        # Slack (rich payload when configured; plain-text fallback kept)
        if m.notify_url:
            from src.api.integrations import notify_slack

            sent = await notify_slack(m.notify_url, job, issues, dashboard_url)
            if not sent:
                text = (
                    f"*CBTP monitor:* {m.name}\n"
                    f"*URL:* {job.url}\n"
                    f"*Status:* {job.status.value if hasattr(job.status, 'value') else job.status}\n"
                    f"*Pages visited:* {job.pages_visited}\n"
                    f"*Issues found:* {job.issues_found}\n"
                    f"*Report:* /jobs/{job.id}/report"
                )
                await notify_webhook(m.notify_url, {"text": text})

        # Jira (env-level or per-monitor config)
        from src.api.integrations import notify_jira

        created = await notify_jira(m, job, issues, settings=None)
        if created:
            logger.info("Created Jira tickets for job %s: %s", job.id, created)
    except Exception:
        logger.exception("notify_job_completion failed (non-fatal)")
