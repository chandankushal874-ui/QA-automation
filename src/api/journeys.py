"""Journeys API — owner-scoped CRUD + execution for multi-step user flows.

A journey belongs to a job (target URL) and an owner (via job.owner_id and a
denormalized owner_id for fast tenant isolation). Running a journey enqueues a
background execution (RQ when Redis is available, else an in-process daemon
thread — same dispatch pattern as crawl jobs).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.auth import get_current_user, get_db_dep
from src.models.database import (
    Job,
    Journey,
    JourneyRun,
    JourneyStatus,
    PlatformUser,
    UserRole,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/journeys", tags=["journeys"])

VALID_ACTIONS = {"goto", "fill", "click", "wait", "assert", "login", "screenshot"}


# ── helpers ─────────────────────────────────────────────────────────────────


def _get_owned_journey(db: Session, journey_id: str, user: PlatformUser) -> Journey:
    try:
        jid = uuid.UUID(journey_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Journey not found") from None
    journey = db.query(Journey).filter_by(id=jid).first()
    if not journey:
        raise HTTPException(status_code=404, detail="Journey not found")
    if user.role != UserRole.ADMIN.value:
        # Owner isolation: prefer journey.owner_id; fall back to job.owner_id.
        owner = getattr(journey, "owner_id", None)
        if not owner and journey.job_id:
            job = db.query(Job).filter_by(id=journey.job_id).first()
            owner = job.owner_id if job else None
        if not owner or str(owner) != str(user.id):
            raise HTTPException(status_code=404, detail="Journey not found")
    return journey


def _journey_dict(j: Journey) -> dict[str, Any]:
    return {
        "id": str(j.id),
        "owner_id": str(j.owner_id) if j.owner_id else None,
        "job_id": str(j.job_id) if j.job_id else None,
        "name": j.name,
        "description": j.description,
        "status": j.status.value if hasattr(j.status, "value") else str(j.status),
        "priority": j.priority,
        "is_critical_path": j.is_critical_path,
        "steps": j.steps,
        "results": j.results,
        "console_errors": j.console_errors,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "completed_at": j.completed_at.isoformat() if j.completed_at else None,
        "duration_seconds": j.duration_seconds,
    }


def _validate_steps(steps: list[dict[str, Any]]) -> None:
    if not isinstance(steps, list) or not steps:
        raise HTTPException(status_code=400, detail="steps must be a non-empty list")
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise HTTPException(status_code=400, detail=f"step {i} must be an object")
        action = str(s.get("action", "")).lower()
        if action not in VALID_ACTIONS:
            raise HTTPException(status_code=400, detail=f"step {i}: unknown action '{action}'")
        if action in ("fill", "click", "assert") and not s.get("selector"):
            raise HTTPException(status_code=400, detail=f"step {i} ({action}) requires 'selector'")
        if action == "goto" and not s.get("url"):
            raise HTTPException(status_code=400, detail=f"step {i} (goto) requires 'url'")
        if action == "login" and not s.get("username"):
            raise HTTPException(status_code=400, detail=f"step {i} (login) requires 'username'")


# ── schemas ────────────────────────────────────────────────────────────────


class CreateJourneyRequest(BaseModel):
    job_id: str = Field(..., description="Target job (supplies base URL + isolation)")
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    steps: list[dict[str, Any]]
    browser_matrix: dict[str, bool] = Field(
        default_factory=lambda: {"chromium": True, "firefox": False, "webkit": False}
    )
    stop_on_failure: bool = False
    priority: int = 0
    is_critical_path: bool = False


class UpdateJourneyRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    steps: list[dict[str, Any]] | None = None
    stop_on_failure: bool | None = None


# ── CRUD ────────────────────────────────────────────────────────────────────


@router.post("", status_code=201)
async def create_journey(
    req: CreateJourneyRequest,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    from src.api.app import _get_owned_job

    _validate_steps(req.steps)
    job = _get_owned_job(db, req.job_id, user)

    journey = Journey(
        owner_id=job.owner_id,
        job_id=job.id,
        name=req.name.strip(),
        description=req.description,
        steps=req.steps,
        status=JourneyStatus.PLANNED,
        priority=req.priority,
        is_critical_path=req.is_critical_path,
        # browser_matrix + stop_on_failure ride in config-like fields (console_errors JSON)
        console_errors={"browser_matrix": req.browser_matrix, "stop_on_failure": req.stop_on_failure},
        results={},
    )
    db.add(journey)
    db.commit()
    db.refresh(journey)
    return _journey_dict(journey)


@router.get("")
async def list_journeys(
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    q = db.query(Journey)
    if user.role != UserRole.ADMIN.value:
        q = q.filter(Journey.owner_id == user.id)
    rows = q.order_by(Journey.created_at.desc()).limit(500).all()
    return {"journeys": [_journey_dict(j) for j in rows], "total": len(rows)}


@router.get("/{journey_id}")
async def get_journey(
    journey_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    return _journey_dict(_get_owned_journey(db, journey_id, user))


@router.patch("/{journey_id}")
async def update_journey(
    journey_id: str,
    req: UpdateJourneyRequest,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    journey = _get_owned_journey(db, journey_id, user)
    data = req.model_dump(exclude_unset=True)
    if "steps" in data and data["steps"] is not None:
        _validate_steps(data["steps"])
    if "stop_on_failure" in data and data["stop_on_failure"] is not None:
        cfg = dict(journey.console_errors or {})
        cfg["stop_on_failure"] = data.pop("stop_on_failure")
        journey.console_errors = cfg
    for field, value in data.items():
        setattr(journey, field, value)
    db.commit()
    db.refresh(journey)
    return _journey_dict(journey)


@router.delete("/{journey_id}")
async def delete_journey(
    journey_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    journey = _get_owned_journey(db, journey_id, user)
    db.delete(journey)
    db.commit()
    return {"status": "deleted", "journey_id": str(journey.id)}


# ── run ─────────────────────────────────────────────────────────────────────


def run_journey_background(run_id: str) -> None:
    """Background entry point: load the run, execute across browsers, persist."""
    from src.api.app import SessionLocal

    db = SessionLocal()
    try:
        run = db.query(JourneyRun).filter_by(id=uuid.UUID(run_id)).first()
        if not run:
            logger.error("JourneyRun %s not found", run_id)
            return
        journey = db.query(Journey).filter_by(id=run.journey_id).first()
        if not journey:
            return
        job = db.query(Job).filter_by(id=journey.job_id).first()
        base_url = job.url if job else ""

        run.status = "running"
        run.started_at = datetime.now(UTC)
        db.commit()

        cfg = dict(journey.console_errors or {})
        matrix = cfg.get("browser_matrix") or {"chromium": True}
        stop_on_failure = bool(cfg.get("stop_on_failure"))
        browsers = [b for b, enabled in matrix.items() if enabled] or ["chromium"]

        results: dict[str, Any] = {}
        all_passed = True
        for browser in browsers:
            try:
                outcome = asyncio.run(_run_single_browser(journey.steps, base_url, browser, stop_on_failure))
                results[browser] = outcome.to_dict()
                if not outcome.passed:
                    all_passed = False
            except Exception as e:
                logger.exception("journey run %s browser %s crashed", run_id, browser)
                results[browser] = {"browser": browser, "passed": False, "error": str(e), "steps": []}
                all_passed = False

        run.results = results
        run.status = "passed" if all_passed else "failed"
        run.completed_at = datetime.now(UTC)
        db.commit()

        journey.status = JourneyStatus.PASSED if all_passed else JourneyStatus.FAILED
        journey.results = results
        journey.started_at = run.started_at
        journey.completed_at = run.completed_at
        if run.started_at and run.completed_at:
            journey.duration_seconds = (run.completed_at - run.started_at).total_seconds()
        db.commit()
    except Exception:
        logger.exception("journey run %s failed", run_id)
        try:
            run.status = "error"
            run.completed_at = datetime.now(UTC)
            db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


async def _run_single_browser(
    steps: list[dict[str, Any]],
    base_url: str,
    browser: str,
    stop_on_failure: bool,
):
    from src.analysis.journey_runner import execute_journey
    from src.browser.factory import BrowserConfig, BrowserContextFactory

    async with BrowserContextFactory() as factory:
        page = await factory.create_page(BrowserConfig(engine=browser, headless=True))
        try:
            return await execute_journey(
                page.page,
                steps,
                base_url=base_url,
                browser=browser,
                stop_on_failure=stop_on_failure,
            )
        finally:
            await page.close()


@router.post("/{journey_id}/run", status_code=201)
async def run_journey(
    journey_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    journey = _get_owned_journey(db, journey_id, user)

    run = JourneyRun(journey_id=journey.id, status="queued", results={})
    db.add(run)
    journey.status = JourneyStatus.RUNNING
    db.commit()
    db.refresh(run)

    # Dispatch: RQ when Redis available, else in-process thread (mirrors crawl).
    enqueued = False
    try:
        import redis as _redis
        from rq import Queue as _Queue

        from src.config import settings

        _rc = _redis.Redis.from_url(settings.redis_url)
        _rc.ping()
        _Queue("default", connection=_rc).enqueue(
            "src.api.journeys.run_journey_background", str(run.id), job_timeout=3600
        )
        enqueued = True
    except Exception as e:
        logger.warning("RQ enqueue for journey run failed (%s) — thread fallback", e)

    if not enqueued:
        import threading

        threading.Thread(target=run_journey_background, args=(str(run.id),), daemon=True).start()

    return {"run_id": str(run.id), "journey_id": str(journey.id), "status": "queued"}


@router.get("/{journey_id}/run/{run_id}")
async def get_run(
    journey_id: str,
    run_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    _get_owned_journey(db, journey_id, user)  # enforce ownership
    try:
        rid = uuid.UUID(run_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Run not found") from None
    run = db.query(JourneyRun).filter_by(id=rid, journey_id=uuid.UUID(journey_id)).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "run_id": str(run.id),
        "journey_id": str(run.journey_id),
        "status": run.status,
        "results": run.results,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }
