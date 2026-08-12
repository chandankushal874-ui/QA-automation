"""Append-only audit logging (Phase 1).

Safe to call from any request path. Failures never break the main request:
if the audit insert fails, we log a warning and continue.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def client_ip(request: Any | None) -> str | None:
    if request is None:
        return None
    try:
        forwarded = request.headers.get("x-forwarded-for") or ""
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
        if request.client:
            return (request.client.host or "")[:64]
    except Exception:
        return None
    return None


def client_ua(request: Any | None) -> str | None:
    if request is None:
        return None
    try:
        ua = request.headers.get("user-agent") or ""
        return ua[:512] if ua else None
    except Exception:
        return None


def record_audit(
    db: Session,
    *,
    action: str,
    actor_id: UUID | str | None = None,
    actor_username: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    success: bool = True,
    detail: dict[str, Any] | None = None,
    request: Any | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Insert one audit row. No-op if audit logging disabled or DB errors."""
    try:
        from src.config import settings

        if not getattr(settings, "audit_log_enabled", True):
            return
    except Exception:
        pass

    try:
        import uuid as _uuid

        from src.models.database import AuditEvent

        aid: _uuid.UUID | None
        if actor_id is None:
            aid = None
        elif isinstance(actor_id, _uuid.UUID):
            aid = actor_id
        else:
            try:
                aid = _uuid.UUID(str(actor_id))
            except (ValueError, TypeError):
                aid = None

        event = AuditEvent(
            actor_id=aid,
            actor_username=(actor_username or None),
            action=action[:80],
            resource_type=(resource_type[:50] if resource_type else None),
            resource_id=(str(resource_id)[:80] if resource_id else None),
            ip_address=ip_address or client_ip(request),
            user_agent=user_agent or client_ua(request),
            success=bool(success),
            detail=dict(detail or {}),
        )
        db.add(event)
        db.commit()
    except Exception as e:
        logger.warning("audit log write failed (non-fatal): %s", e)
        with contextlib.suppress(Exception):
            db.rollback()
