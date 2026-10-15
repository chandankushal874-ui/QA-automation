"""Internal job-dispatch helper for scheduled monitors.

Reuses ``app._create_job_core`` — the exact production create path (slot
limits, tenant isolation, RQ enqueue, audit) — so there is ONE code path
for creating crawls. Never imported by request handlers directly.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def create_job_as_user(
    db: Session,
    owner_id: uuid.UUID,
    url: str,
    browser_matrix: dict[str, bool] | None = None,
    max_pages: int = 25,
    max_depth: int = 3,
) -> str | None:
    """Create a crawl owned by owner_id via the normal API path.

    Returns the new job id, or None when the platform refused the job
    (e.g. the owner's concurrent-slot limit is full). Slot-limit refusals
    are expected and logged at info — monitors simply retry next interval.
    """
    from fastapi import HTTPException

    from src.api.app import _create_job_core
    from src.models.database import PlatformUser

    owner = db.query(PlatformUser).filter(PlatformUser.id == owner_id).first()
    if not owner or not owner.is_active:
        logger.warning("Monitor dispatch: owner %s missing/inactive", owner_id)
        return None

    try:
        response = _create_job_core(
            db=db,
            owner=owner,
            url=url,
            scope="same-domain",
            browser_matrix=browser_matrix
            or {"chromium": True, "firefox": False, "webkit": False},
            max_pages=max_pages,
            max_depth=max_depth,
            max_time_seconds=600,
            repo_url=None,
            config={"source": "monitor"},
            credentials=None,
            http_request=None,
        )
        return str(response.id)
    except HTTPException as e:
        # 429 slot limit and 400 security gate are legitimate refusals
        logger.info("Monitor job refused for owner %s: %s %s", owner_id, e.status_code, e.detail)
        return None
    except Exception:
        logger.exception("Monitor dispatch crashed for owner %s", owner_id)
        return None
