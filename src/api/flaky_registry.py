"""Flaky-issue registry: cross-crawl flakiness detection + quarantine.

Problem: the engine already retries an error twice within one crawl and tags
non-reproducing issues [FLAKY] — but that evidence is discarded when the job
ends. A race-condition error that shows up on 1 of 3 *crawl runs* (not just
1 of 3 in-crawl retries) looks "real" every time it reappears, burns QA
review time, and (worst) fails PR gates on noise.

Design (production-grade):
- FlakyIssue: one row per (owner, fingerprint). Each crawl records a
  sighting (seen=True) or its absence is implied by the crawl finishing
  without that fingerprint. A bounded ring of the last N run outcomes
  feeds a simple statistical rule:

      flaky_score = (# flips between seen/unseen) / (# transitions)
      status:     observed → suspected → quarantined → confirmed

- Quarantined/confirmed issues are EXCLUDED from CI gate severity counts
  (scripts/ci_gate.py reads quarantine status via the report API) but are
  always listed in a dedicated report bucket, so flakiness stays visible.

- Tenant isolation: rows are keyed by owner_id — User A's flaky fingerprints
  never influence User B's verdicts.

- Non-fatal by design: registry failures never break the crawl pipeline.
"""

from __future__ import annotations

import itertools
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.api.auth import get_current_user, get_db_dep
from src.models.database import FlakyIssue, PlatformUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me/flaky-registry", tags=["flaky-registry"])

# How many recent run outcomes to consider per fingerprint.
HISTORY_WINDOW = 20
# Minimum sample before any verdict (avoid 1-run snap judgments).
MIN_SAMPLES_FOR_VERDICT = 3
# Flip rate over the window that promotes observed → suspected → quarantined.
SUSPECTED_FLIP_RATE = 0.3
QUARANTINE_FLIP_RATE = 0.5

STATUS_ORDER = ("observed", "suspected", "quarantined", "confirmed")


def _flaky_severity(flip_rate: float, samples: int) -> str:
    """Pure verdict function — unit-tested independently of storage."""
    if samples < MIN_SAMPLES_FOR_VERDICT:
        return "observed"
    if flip_rate >= QUARANTINE_FLIP_RATE:
        return "quarantined"
    if flip_rate >= SUSPECTED_FLIP_RATE:
        return "suspected"
    return "observed"


def compute_flip_rate(history: list[bool]) -> tuple[float, int]:
    """Flip rate over a bounded history of seen=True/False outcomes.

    A "flip" is a transition between consecutive runs (seen↔unseen).
    Returns (flip_rate, transitions) where flip_rate is flips/max(1, transitions).
    Empty/1-item histories give (0.0, 0).
    """
    h = list(history)[-HISTORY_WINDOW:]
    if len(h) < 2:
        return 0.0, 0
    transitions = len(h) - 1
    flips = sum(1 for a, b in itertools.pairwise(h) if a != b)
    return flips / transitions, transitions


class RunOutcome(BaseModel):
    """Result of one crawl for one fingerprint."""

    fingerprint: str
    seen: bool
    title: str = ""
    category: str = ""
    severity: str = ""
    first_seen_url: str = ""
    sample_url: str = ""


def _append_bounded(history: list[Any], outcome: bool) -> list[Any]:
    """Append and hard-cap the ring at HISTORY_WINDOW (inclusive of new item)."""
    h = list(history or [])
    h.append(outcome)
    return h[-HISTORY_WINDOW:]


def record_run_outcomes(db: Session, owner_id: Any, job: Any, issues: list[Any]) -> dict[str, str]:
    """Update the registry after a completed crawl.

    `issues` are the crawl's triaged Issue rows. For each fingerprint:
      - seen → append True; update sample metadata
      - not seen (known fingerprint absent from this run) → append False
    Then recompute flip rate + status. Never raises (best-effort).
    Returns {fingerprint: new_status}.
    """
    results: dict[str, str] = {}
    try:
        run_fingerprints = {str(getattr(i, "fingerprint", "")): i for i in issues}
        rows = db.query(FlakyIssue).filter(FlakyIssue.owner_id == owner_id).all()
        row_by_fp = {r.fingerprint: r for r in rows}

        # 1) fingerprints observed this run
        for fp, issue in run_fingerprints.items():
            if not fp:
                continue
            row = row_by_fp.get(fp)
            if row is None:
                row = FlakyIssue(
                    owner_id=owner_id,
                    fingerprint=fp,
                    title=str(getattr(issue, "title", ""))[:200],
                    category=str(getattr(getattr(issue, "category", None), "value", getattr(issue, "category", "")))[:50],
                    severity=str(getattr(getattr(issue, "severity", None), "value", getattr(issue, "severity", "")))[:20],
                    first_seen_url=str(getattr(issue, "first_seen_url", ""))[:2048],
                    history=[True],
                    status="observed",
                )
                db.add(row)
                row_by_fp[fp] = row
            else:
                row.history = _append_bounded(row.history, True)
            row.sample_url = str(getattr(job, "url", ""))[:2048]
            row.last_seen_at = datetime.now(UTC)

        # 2) known fingerprints this crawl did NOT see (a "pass" outcome)
        for fp, row in row_by_fp.items():
            if fp in run_fingerprints:
                continue
            row.history = _append_bounded(row.history, False)

        # 3) recompute verdicts
        for fp, row in row_by_fp.items():
            flip_rate, samples = compute_flip_rate(list(row.history or []))
            if row.status == "confirmed":
                results[fp] = row.status  # human decisions are sticky
                continue
            new_status = _flaky_severity(flip_rate, samples)
            row.status = new_status
            row.flip_rate = flip_rate
            row.samples = len(list(row.history or []))
            results[fp] = new_status

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("flaky registry update failed (non-fatal)")
    return results


def quarantine_status_map(db: Session, owner_id: Any) -> dict[str, dict[str, Any]]:
    """fingerprint → {status, flip_rate, samples} for all of this owner's rows."""
    try:
        rows = db.query(FlakyIssue).filter(FlakyIssue.owner_id == owner_id).all()
        return {
            r.fingerprint: {
                "status": r.status,
                "flip_rate": r.flip_rate,
                "samples": r.samples,
            }
            for r in rows
        }
    except Exception:
        return {}


def is_gate_exempt(db: Session, owner_id: Any, fingerprint: str) -> bool:
    """True when a fingerprint is quarantined/confirmed (excluded from gates)."""
    m = quarantine_status_map(db, owner_id).get(fingerprint)
    return bool(m and m["status"] in ("quarantined", "confirmed"))


# ── API ─────────────────────────────────────────────────────────────────────


def _row_dict(r: FlakyIssue) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "fingerprint": r.fingerprint,
        "title": r.title,
        "category": r.category,
        "severity": r.severity,
        "first_seen_url": r.first_seen_url,
        "sample_url": r.sample_url,
        "status": r.status,
        "flip_rate": r.flip_rate,
        "samples": r.samples,
        "history": list(r.history or [])[-HISTORY_WINDOW:],
        "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("")
async def list_registry(
    status: str | None = None,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """List the caller's flaky registry (own rows only — tenant isolated)."""
    q = db.query(FlakyIssue).filter(FlakyIssue.owner_id == user.id)
    if status:
        if status not in STATUS_ORDER:
            raise HTTPException(400, detail=f"status must be one of {STATUS_ORDER}")
        q = q.filter(FlakyIssue.status == status)
    rows = q.order_by(FlakyIssue.last_seen_at.desc().nullslast()).limit(500).all()
    return {"registry": [_row_dict(r) for r in rows], "total": len(rows)}


@router.post("/{fingerprint}/confirm")
async def confirm_flaky(
    fingerprint: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Human decision: mark a fingerprint as confirmed flaky (sticky)."""
    row = (
        db.query(FlakyIssue)
        .filter(FlakyIssue.owner_id == user.id, FlakyIssue.fingerprint == fingerprint)
        .first()
    )
    if not row:
        raise HTTPException(404, detail="Fingerprint not found")
    row.status = "confirmed"
    db.commit()
    return {"status": row.status, "fingerprint": fingerprint}


@router.post("/{fingerprint}/reset")
async def reset_flaky(
    fingerprint: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Human decision: clear quarantine (issue is real again)."""
    row = (
        db.query(FlakyIssue)
        .filter(FlakyIssue.owner_id == user.id, FlakyIssue.fingerprint == fingerprint)
        .first()
    )
    if not row:
        raise HTTPException(404, detail="Fingerprint not found")
    row.status = "observed"
    row.history = []
    row.samples = 0
    row.flip_rate = 0.0
    db.commit()
    return {"status": row.status, "fingerprint": fingerprint}


@router.delete("/{fingerprint}")
async def delete_flaky(
    fingerprint: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Remove a fingerprint from the registry entirely."""
    row = (
        db.query(FlakyIssue)
        .filter(FlakyIssue.owner_id == user.id, FlakyIssue.fingerprint == fingerprint)
        .first()
    )
    if not row:
        raise HTTPException(404, detail="Fingerprint not found")
    db.delete(row)
    db.commit()
    return {"status": "deleted", "fingerprint": fingerprint}
