"""Cross-job analytics: trends, breakdowns, target rollups, flaky stats.

Turns per-job data into time series and aggregates (owner-scoped, admin sees
all). v1 computes live from the existing tables â€” no rollup tables, no new
indexes beyond what exists (jobs.created_at, issues.created_at,
issues.job_id+fingerprint are all indexed).

Endpoints:
    GET /api/analytics/trends?days=30   daily crawl/issue series
    GET /api/analytics/issues?days=30   severity/category breakdowns + top recurring
    GET /api/analytics/targets?days=30  per-target rollups
    GET /api/analytics/flaky?days=30   flaky registry trend + top flaky

Date bucketing uses func.date() (works on both SQLite and PostgreSQL).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.api.auth import get_current_user, get_db_dep
from src.models.database import (
    FlakyIssue,
    Issue,
    Job,
    JobStatus,
    PlatformUser,
    UserRole,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _window(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; normalize to UTC for comparisons."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _owner_job_query(db: Session, user: PlatformUser):
    q = db.query(Job)
    if user.role != UserRole.ADMIN.value:
        q = q.filter(Job.owner_id == user.id)
    return q


def _date_str(row: Any) -> str:
    """Normalize a date-ish value (date or str) from the DB to ISO date."""
    d = row[0] if isinstance(row, tuple) else row
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, str):
        return d[:10]
    return str(d)


def _zero_fill(dates_seen: dict[str, dict[str, Any]], days: int) -> list[dict[str, Any]]:
    """Fill missing days in the window with zero rows, ordered ascending."""
    today = datetime.now(UTC).date()
    series: list[dict[str, Any]] = []
    for offset in range(days - 1, -1, -1):
        d = (today - timedelta(days=offset)).isoformat()
        series.append(dates_seen.get(d, {"date": d, "crawls": 0, "completed": 0, "failed": 0,
                                         "issues": 0, "blocking": 0, "functional": 0,
                                         "visual": 0, "advisory": 0, "pages_visited": 0}))
    return series


@router.get("/trends")
async def analytics_trends(
    days: int = Query(default=30, ge=1, le=365),
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Daily crawl + issue series over the window (owner-scoped)."""
    since = _window(days)

    base = _owner_job_query(db, user)
    jobs = base.filter(Job.created_at >= since).all()
    job_ids = [j.id for j in jobs]

    # Bucket jobs by local day.
    by_day: dict[str, dict[str, Any]] = {}
    for j in jobs:
        d = j.created_at.date().isoformat() if j.created_at else None
        if not d:
            continue
        row = by_day.setdefault(d, {"date": d, "crawls": 0, "completed": 0, "failed": 0,
                                   "issues": 0, "blocking": 0, "functional": 0,
                                   "visual": 0, "advisory": 0, "pages_visited": 0})
        row["crawls"] += 1
        if j.status == JobStatus.COMPLETED:
            row["completed"] += 1
        elif j.status == JobStatus.FAILED:
            row["failed"] += 1
        row["pages_visited"] += j.pages_visited or 0

    # Bucket issues by day (join on job_ids, issues carry created_at).
    if job_ids:
        sev_day = (
            db.query(func.date(Issue.created_at), Issue.severity, func.count(Issue.id))
            .filter(Issue.job_id.in_(job_ids), Issue.created_at >= since)
            .group_by(func.date(Issue.created_at), Issue.severity)
            .all()
        )
        for d, severity, count in sev_day:
            ds = _date_str((d, severity))
            if ds not in by_day:
                continue
            sev_name = getattr(severity, "value", str(severity))
            by_day[ds]["issues"] += count
            if sev_name in ("blocking", "functional", "visual", "advisory"):
                by_day[ds][sev_name] += count

    series = _zero_fill(by_day, days)

    completed = sum(r["completed"] for r in series)
    failed = sum(r["failed"] for r in series)
    decided = completed + failed
    pass_rate = round(completed / decided, 4) if decided else None

    return {
        "days": days,
        "series": series,
        "totals": {
            "crawls": sum(r["crawls"] for r in series),
            "issues": sum(r["issues"] for r in series),
            "pages_visited": sum(r["pages_visited"] for r in series),
            "pass_rate": pass_rate,
        },
    }


@router.get("/issues")
async def analytics_issues(
    days: int = Query(default=30, ge=1, le=365),
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Severity + category breakdowns and top recurring fingerprints in the window."""
    since = _window(days)

    job_ids = [j.id for j in _owner_job_query(db, user).with_entities(Job.id).all()]
    if not job_ids:
        return {"days": days, "by_severity": {}, "by_category": {}, "top_recurring": []}

    q = db.query(Issue).filter(Issue.job_id.in_(job_ids), Issue.created_at >= since)

    by_severity: dict[str, int] = {}
    for sev, count in q.with_entities(Issue.severity, func.count(Issue.id)).group_by(Issue.severity).all():
        by_severity[getattr(sev, "value", str(sev))] = count

    by_category: dict[str, int] = {}
    for cat, count in q.with_entities(Issue.category, func.count(Issue.id)).group_by(Issue.category).all():
        by_category[getattr(cat, "value", str(cat))] = count

    top_recurring = (
        db.query(
            Issue.fingerprint,
            func.count(Issue.id).label("occurrences"),
            func.max(Issue.severity).label("severity"),
            Issue.title,
            func.max(Issue.created_at).label("last_seen"),
        )
        .filter(Issue.job_id.in_(job_ids), Issue.created_at >= since)
        .group_by(Issue.fingerprint, Issue.title)
        .order_by(func.count(Issue.id).desc())
        .limit(10)
        .all()
    )

    return {
        "days": days,
        "by_severity": by_severity,
        "by_category": by_category,
        "top_recurring": [
            {
                "fingerprint": fp,
                "title": title or "",
                "occurrences": occ,
                "severity": getattr(sev, "value", str(sev)),
                "last_seen": last.isoformat() if last else None,
            }
            for fp, occ, sev, title, last in top_recurring
        ],
    }


@router.get("/targets")
async def analytics_targets(
    days: int = Query(default=30, ge=1, le=365),
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Per-target (job.url) rollups over the window."""
    since = _window(days)

    jobs = _owner_job_query(db, user).filter(Job.created_at >= since).all()
    job_ids = [j.id for j in jobs]

    targets: dict[str, dict[str, Any]] = {}
    by_job_url: dict[Any, list[Any]] = {}
    for j in jobs:
        by_job_url.setdefault(j.url, []).append(j)

    if job_ids:
        issue_rows = (
            db.query(
                Issue.job_id,
                func.count(func.distinct(Issue.fingerprint)).label("fingerprints"),
                func.count(Issue.id).label("issues"),
            )
            .filter(Issue.job_id.in_(job_ids))
            .group_by(Issue.job_id)
            .all()
        )
        by_job = {job_id: {"fingerprints": fp, "issues": n} for job_id, fp, n in issue_rows}

    for url, url_jobs in by_job_url.items():
        crawls = len(url_jobs)
        issues = sum(by_job.get(j.id, {}).get("issues", 0) for j in url_jobs)
        fingerprints = sum(by_job.get(j.id, {}).get("fingerprints", 0) for j in url_jobs)
        completed = sum(1 for j in url_jobs if j.status == JobStatus.COMPLETED)
        targets[url] = {
            "url": url,
            "crawls": crawls,
            "completed": completed,
            "issues": issues,
            "distinct_fingerprints": fingerprints,
            "avg_issues_per_crawl": round(issues / crawls, 2) if crawls else 0,
            "last_crawl_status": getattr(url_jobs[0].status, "value", str(url_jobs[0].status)) if url_jobs else None,
            "last_crawled_at": max((j.created_at for j in url_jobs if j.created_at), default=None),
        }

    # Sort by issue volume, descending.
    ordered = sorted(targets.values(), key=lambda t: (-t["issues"], t["url"]))[:25]
    return {"days": days, "targets": ordered, "total": len(targets)}


@router.get("/flaky")
async def analytics_flaky(
    days: int = Query(default=30, ge=1, le=365),
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Flaky-registry trend + current top flaky fingerprints (owner-scoped)."""
    since = _window(days)

    q = db.query(FlakyIssue).filter(FlakyIssue.owner_id == user.id)
    rows = q.all()

    quarantined = sum(1 for r in rows if r.status in ("quarantined", "confirmed"))
    suspected = sum(1 for r in rows if r.status == "suspected")
    new_in_window = sum(1 for r in rows if _aware(r.created_at) and _aware(r.created_at) >= since)

    # Daily new-flaky counts over the window.
    by_day: dict[str, int] = {}
    for r in rows:
        rc = _aware(r.created_at)
        if rc and rc >= since:
            d = rc.date().isoformat()
            by_day[d] = by_day.get(d, 0) + 1

    today = datetime.now(UTC).date()
    series = []
    for offset in range(days - 1, -1, -1):
        d = (today - timedelta(days=offset)).isoformat()
        series.append({"date": d, "new": by_day.get(d, 0)})

    top = sorted(rows, key=lambda r: -(r.flip_rate or 0))[:10]

    return {
        "days": days,
        "totals": {
            "registry_size": len(rows),
            "quarantined": quarantined,
            "suspected": suspected,
            "new_in_window": new_in_window,
        },
        "series": series,
        "top_flaky": [
            {
                "fingerprint": r.fingerprint,
                "title": r.title or "",
                "status": r.status,
                "flip_rate": round(r.flip_rate or 0.0, 4),
                "samples": r.samples,
            }
            for r in top
        ],
    }
