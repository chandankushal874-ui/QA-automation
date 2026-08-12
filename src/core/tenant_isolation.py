"""Per-owner (tenant) isolation helpers for crawl artifacts and Redis keys.

True multi-user isolation: each platform user (identified by username+password
login → stable user UUID) owns a sealed namespace for:
  - on-disk artifacts (reports, baselines, traces, HAR, video)
  - Redis frontier / crawl keys
  - RQ crawl queues (optional per-user queue name)

User A's crawler must never read or write User B's jobs, keys, or files.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)


def owner_namespace(owner_id: str | UUID | None) -> str:
    """Stable short namespace for an owner. Shared 'legacy' only if no owner."""
    if owner_id is None or str(owner_id).strip() == "":
        return "legacy"
    raw = str(owner_id).strip().lower().replace("-", "")
    # Prefer full uuid hex when possible; else hash
    if re.fullmatch(r"[0-9a-f]{32}", raw):
        return raw[:16]
    return hashlib.sha256(str(owner_id).encode("utf-8")).hexdigest()[:16]


def resolve_job_owner_id(job: Any) -> str | None:
    """Return the authoritative owner UUID string for a job.

    Prefer Job.owner_id; fall back to isolation stamp in job.config
    (defense against partial migrations).
    """
    owner = getattr(job, "owner_id", None)
    if owner is not None and str(owner).strip():
        return str(owner)
    cfg = getattr(job, "config", None) or {}
    if isinstance(cfg, dict):
        iso = cfg.get("isolation") or {}
        stamped = iso.get("owner_id")
        if stamped:
            return str(stamped)
    return None


def assert_job_owner_isolation(job: Any) -> None:
    """Ensure isolation stamp matches job.owner_id when both are present.

    Raises ValueError if a cross-tenant stamp mismatch is detected (would
    indicate corruption or attempted reassignment).
    """
    owner = getattr(job, "owner_id", None)
    cfg = getattr(job, "config", None) or {}
    if not isinstance(cfg, dict):
        return
    iso = cfg.get("isolation") or {}
    stamped = iso.get("owner_id")
    if owner is not None and stamped and str(owner) != str(stamped):
        raise ValueError(
            f"Isolation breach: job {getattr(job, 'id', '?')} owner_id={owner} "
            f"does not match isolation.owner_id={stamped}"
        )


def tenant_root(owner_id: str | UUID | None, base: str = "data") -> Path:
    """Root directory for all on-disk artifacts of one platform user."""
    return Path(base) / "tenants" / owner_namespace(owner_id)


def job_artifact_dirs(owner_id: str | UUID | None, job_id: str | UUID) -> dict[str, str]:
    """Return isolated absolute-relative paths for one job under its owner."""
    jid = str(job_id)
    root = tenant_root(owner_id)
    paths = {
        "tenant_root": str(root),
        "job_root": str(root / "jobs" / jid),
        "traces": str(root / "jobs" / jid / "traces"),
        "videos": str(root / "jobs" / jid / "videos"),
        "har": str(root / "jobs" / jid / "har"),
        "snapshots": str(root / "jobs" / jid / "snapshots"),
        "allure": str(root / "jobs" / jid / "allure"),
        "report_html": str(root / "jobs" / jid / f"report_{jid}.html"),
        "baselines": str(root / "jobs" / jid / "baselines"),
        "frontier": str(root / "jobs" / jid / "frontier"),
    }
    # Ensure dirs exist (safe, idempotent)
    for key in ("traces", "videos", "har", "snapshots", "allure", "baselines", "frontier"):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)
    return paths


def redis_job_prefix(owner_id: str | UUID | None, job_id: str | UUID) -> str:
    """Prefix for Redis keys belonging to one owner's job."""
    return f"t:{owner_namespace(owner_id)}:j:{job_id}"


def frontier_key_prefix(owner_id: str | UUID | None, job_id: str | UUID) -> str:
    """Redis frontier key prefix — never collides across tenants even if job ids leaked."""
    return f"frontier:{owner_namespace(owner_id)}:{job_id}"


def crawl_queue_name(owner_id: str | UUID | None) -> str:
    """Per-user RQ queue so User A jobs never share a worker queue with User B.

    Workers should listen to both ``default`` (legacy) and ``crawl:*`` patterns
    or subscribe to all tenant queues. Dispatch always targets this name when
    owner_id is known.
    """
    if owner_id is None or str(owner_id).strip() == "":
        return "default"
    return f"crawl:{owner_namespace(owner_id)}"


def redis_url_for_owner(base_redis_url: str, owner_id: str | UUID | None) -> str:
    """Map each platform user to a dedicated Redis logical database (0–15).

    Redis default maxdatabases=16. We pin:
      db 0  → shared/system (RQ default queue stays on 0 for workers)
      db 1–15 → tenants (stable hash of owner_id)

    Frontier data for crawls uses tenant-prefixed keys on the *same* connection
    when a dedicated DB cannot be used; when possible we open a second client
    on the tenant DB so keyspaces never mix.
    """
    if not base_redis_url:
        return base_redis_url
    if owner_id is None:
        return base_redis_url
    # Honor global toggle
    try:
        from src.config import settings

        if not getattr(settings, "tenant_redis_isolation", True):
            return base_redis_url
    except Exception:
        pass
    ns = owner_namespace(owner_id)
    # Stable 1..15
    digest = int(hashlib.sha256(ns.encode()).hexdigest()[:8], 16)
    db_index = 1 + (digest % 15)
    # Replace /N at end of redis URL
    if "/" in base_redis_url.rsplit("@", 1)[-1]:
        # redis://host:6379/0 → redis://host:6379/{db_index}
        head, _, _tail = base_redis_url.rpartition("/")
        if head and _tail.isdigit():
            return f"{head}/{db_index}"
    if base_redis_url.rstrip("/").endswith(":6379"):
        return f"{base_redis_url.rstrip('/')}/{db_index}"
    return f"{base_redis_url.rstrip('/')}/{db_index}"


def safe_resolve_under_tenant(
    owner_id: str | UUID | None,
    relative_path: str,
    *,
    also_allow_legacy_reports: bool = False,
) -> Path | None:
    """Resolve a path only if it stays under the tenant root.

    By default legacy ``reports/`` is NOT allowed for non-admin callers so
    User A cannot fetch User B screenshots by guessing paths under reports/.
    Admins may pass also_allow_legacy_reports=True.
    """
    # Normalize and block absolute / .. tricks
    candidate = Path(relative_path.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        return None

    tenant = tenant_root(owner_id).resolve()
    try_paths: list[Path] = [
        (tenant / candidate).resolve(),
        (tenant / "jobs" / candidate).resolve(),
    ]
    if also_allow_legacy_reports:
        # Backward compat for jobs created before tenant dirs (admin / legacy only)
        try_paths.append((Path("reports") / candidate).resolve())
        try_paths.append((Path("reports") / "snapshots" / candidate).resolve())

    for full in try_paths:
        try:
            full.relative_to(tenant)
            if full.exists() and full.is_file():
                return full
        except ValueError:
            pass
        if also_allow_legacy_reports:
            try:
                reports = Path("reports").resolve()
                full.relative_to(reports)
                if full.exists() and full.is_file():
                    return full
            except ValueError:
                pass
    return None


def apply_job_isolation_paths(job: Any) -> dict[str, str]:
    """Compute and persist isolation paths into job.config (SQLAlchemy-safe reassignment)."""
    owner_id = getattr(job, "owner_id", None)
    if owner_id is None:
        logger.warning(
            "apply_job_isolation_paths: job %s has no owner_id — using legacy namespace",
            getattr(job, "id", "?"),
        )
    paths = job_artifact_dirs(owner_id, job.id)
    # Baselines stay job-scoped (from job_artifact_dirs) so every run starts
    # from scratch — a re-test of the same URL never reuses previous results.
    cfg = dict(getattr(job, "config", None) or {})
    cfg["isolation"] = {
        "owner_id": str(owner_id) if owner_id else None,
        "tenant": owner_namespace(owner_id),
        "queue": crawl_queue_name(owner_id),
        "paths": paths,
        "level": "user_id",
    }
    job.config = cfg
    return paths


def owners_match(owner_a: str | UUID | None, owner_b: str | UUID | None) -> bool:
    """True if both owner ids refer to the same platform user."""
    if owner_a is None or owner_b is None:
        return False
    return str(owner_a).strip().lower() == str(owner_b).strip().lower()
