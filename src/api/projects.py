"""Projects + visual-baseline diff API.

Owner-scoped project CRUD, baseline capture, and job-vs-baseline comparison.
This is the backend for the visual-regression workflow:

    1. create a Project (owner, slug, base_url, default_branch)
    2. run a crawl of the default branch → POST /api/projects/{id}/baseline
       captures the job's screenshots as the approved baseline
    3. later crawls (e.g. a PR branch) → POST /api/projects/{id}/compare
       diffs each screenshot against the active baseline for that identity
    4. approve a compare to promote current → new baseline

Images are stored in S3/MinIO under tenant-namespaced keys; this module
streams them back through a tenant-checked proxy so the diff viewer never
needs direct object-store access. Every query is owner-scoped (admin override).

Screenshot naming mirrors engine.py (``{safe_url}_{browser}.png``); the helper
:func:`snapshot_filename_for` is the single source of truth for reconstructing
a job's on-disk snapshot path from a URL + browser.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.auth import get_current_user, get_db_dep
from src.core import s3
from src.core.tenant_isolation import owner_namespace
from src.models.database import (
    CrawlState,
    PlatformUser,
    Project,
    UserRole,
    VisualBaseline,
    VisualCompare,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])

_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ── helpers ─────────────────────────────────────────────────────────────────


def slugify(name: str) -> str:
    """Lowercase, non-alnum → '-', collapse + trim. Empty → 'project'."""
    s = _SLUG_RE.sub("-", name.lower()).strip("-")
    return s or "project"


def _get_owned_project(db: Session, project_id: str, user: PlatformUser) -> Project:
    try:
        pid = uuid.UUID(project_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Project not found") from None
    proj = db.query(Project).filter_by(id=pid).first()
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    if user.role != UserRole.ADMIN.value and str(proj.owner_id) != str(user.id):
        # 404 (not 403) to avoid project-id enumeration.
        raise HTTPException(status_code=404, detail="Project not found")
    return proj


def _project_dict(p: Project) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "slug": p.slug,
        "base_url": p.base_url,
        "default_branch": p.default_branch,
        "viewports": p.viewports,
        "ignore_regions": p.ignore_regions,
        "threshold": p.threshold,
        "is_active": p.is_active,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def snapshot_filename_for(url: str, browser: str) -> str:
    """Reconstruct engine.py's on-disk snapshot filename for (url, browser).

    Mirrors src/crawler/engine.py exactly: replace ://, /, ?, = with _; keep
    only the last 100 chars; append ``_{browser}.png``.
    """
    safe_url = url.replace("://", "_").replace("/", "_").replace("?", "_").replace("=", "_")
    if len(safe_url) > 100:
        safe_url = safe_url[-100:]
    return f"{safe_url}_{browser}.png"


def _job_snapshot_dir(job: Any) -> str | None:
    """Return the job's tenant snapshots dir, or None."""
    iso = (getattr(job, "config", None) or {}).get("isolation") or {}
    return (iso.get("paths") or {}).get("snapshots")


def _read_snapshot_bytes(snap_dir: str, filename: str) -> bytes | None:
    import os

    path = os.path.join(snap_dir, filename)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def _artifact_key_for(project: Project, branch: str, kind: str, name: str) -> str:
    return s3.object_key(project.owner_id, project.id, branch, kind, name)


# ── schemas ────────────────────────────────────────────────────────────────


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str | None = None
    base_url: str = Field(..., min_length=1, max_length=2048)
    default_branch: str = Field(default="main", max_length=100)
    viewports: list[dict[str, Any]] = Field(default_factory=lambda: [{"width": 1280, "height": 720}])
    ignore_regions: list[dict[str, Any]] = Field(default_factory=list)
    threshold: float = Field(default=0.1, ge=0.0, le=100.0)


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    default_branch: str | None = None
    viewports: list[dict[str, Any]] | None = None
    ignore_regions: list[dict[str, Any]] | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=100.0)
    is_active: bool | None = None


class CaptureBaselineRequest(BaseModel):
    job_id: str = Field(..., description="Completed crawl job whose screenshots become the baseline")
    branch: str | None = None
    urls: list[str] | None = Field(default=None, description="Optional subset of URLs to baseline")


class CompareRequest(BaseModel):
    job_id: str = Field(..., description="Completed crawl job to diff against the baseline")
    branch: str | None = None


# ── project CRUD ───────────────────────────────────────────────────────────


@router.post("", status_code=201)
async def create_project(
    req: CreateProjectRequest,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    slug = (req.slug or slugify(req.name)).strip() or slugify(req.name)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", slug):
        raise HTTPException(status_code=400, detail="slug must be lowercase alphanumeric + hyphens")
    if (
        db.query(Project)
        .filter(Project.owner_id == user.id, Project.slug == slug)
        .first()
    ):
        raise HTTPException(status_code=409, detail="A project with this slug already exists")
    proj = Project(
        owner_id=user.id,
        name=req.name.strip(),
        slug=slug,
        base_url=req.base_url,
        default_branch=req.default_branch or "main",
        viewports=req.viewports or [{"width": 1280, "height": 720}],
        ignore_regions=req.ignore_regions or [],
        threshold=req.threshold,
        is_active=True,
    )
    db.add(proj)
    db.commit()
    db.refresh(proj)
    return _project_dict(proj)


@router.get("")
async def list_projects(
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    q = db.query(Project)
    if user.role != UserRole.ADMIN.value:
        q = q.filter(Project.owner_id == user.id)
    rows = q.order_by(Project.created_at.desc()).all()
    return {"projects": [_project_dict(p) for p in rows], "total": len(rows)}


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    return _project_dict(_get_owned_project(db, project_id, user))


@router.patch("/{project_id}")
async def update_project(
    project_id: str,
    req: UpdateProjectRequest,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    proj = _get_owned_project(db, project_id, user)
    data = req.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(proj, field, value)
    db.commit()
    db.refresh(proj)
    return _project_dict(proj)


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    proj = _get_owned_project(db, project_id, user)
    db.delete(proj)
    db.commit()
    return {"status": "deleted", "project_id": str(proj.id)}


# ── baselines ──────────────────────────────────────────────────────────────


def _active_baseline(
    db: Session, project: Project, branch: str, url: str, browser: str
) -> VisualBaseline | None:
    return (
        db.query(VisualBaseline)
        .filter(
            VisualBaseline.project_id == project.id,
            VisualBaseline.branch == branch,
            VisualBaseline.url == url,
            VisualBaseline.browser == browser,
            VisualBaseline.status == "active",
        )
        .first()
    )


def _supersede_baselines(db: Session, project: Project, branch: str, url: str, browser: str) -> None:
    rows = (
        db.query(VisualBaseline)
        .filter(
            VisualBaseline.project_id == project.id,
            VisualBaseline.branch == branch,
            VisualBaseline.url == url,
            VisualBaseline.browser == browser,
            VisualBaseline.status == "active",
        )
        .all()
    )
    for r in rows:
        r.status = "superseded"


def _enumerate_job_snapshots(db: Session, job: Any) -> list[tuple[str, str]]:
    """Return [(url, browser), ...] for the job's visited pages x browser matrix.

    A snapshot is diffed for every (url, browser) where the browser is enabled
    in the job matrix. We do not require the snapshot file to exist yet — the
    caller handles missing files as a soft skip.
    """
    browsers = [b for b, enabled in (job.browser_matrix or {}).items() if enabled] or ["chromium"]
    cstates = db.query(CrawlState).filter_by(job_id=job.id).all()
    urls = sorted({cs.url for cs in cstates})
    pairs: list[tuple[str, str]] = []
    for url in urls:
        for b in browsers:
            pairs.append((url, b))
    return pairs


def _store_image(project: Project, branch: str, kind: str, name: str, data: bytes) -> str:
    key = _artifact_key_for(project, branch, kind, name)
    s3.put_bytes(key, data, content_type="image/png")
    return key


@router.post("/{project_id}/baseline", status_code=201)
async def capture_baseline(
    project_id: str,
    req: CaptureBaselineRequest,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Capture a completed job's screenshots as the project's active baseline.

    Runs the S3 upload off-thread (boto3 is sync). Supersedes any existing
    active baseline for each (branch, url, browser) identity it touches.
    """
    from src.api.app import _get_owned_job

    proj = _get_owned_project(db, project_id, user)
    job = _get_owned_job(db, req.job_id, user)
    branch = req.branch or proj.default_branch

    pairs = _enumerate_job_snapshots(db, job)
    if req.urls:
        wanted = set(req.urls)
        pairs = [(u, b) for (u, b) in pairs if u in wanted]

    snap_dir = _job_snapshot_dir(job)
    if not snap_dir:
        raise HTTPException(status_code=400, detail="Job has no snapshot directory")

    created: list[dict[str, Any]] = []
    for url, browser in pairs:
        filename = snapshot_filename_for(url, browser)
        data = await asyncio.to_thread(_read_snapshot_bytes, snap_dir, filename)
        if data is None:
            continue  # soft-skip pages that failed to snapshot
        key = await asyncio.to_thread(_store_image, proj, branch, "baseline", f"{url_hash(url)}_{browser}.png", data)
        image_hash = s3.sha256_bytes(data)

        # Supersede existing active baseline for this identity, then insert new.
        _supersede_baselines(db, proj, branch, url, browser)
        b = VisualBaseline(
            project_id=proj.id,
            branch=branch,
            url=url,
            browser=browser,
            viewport={"width": 0, "height": 0},  # full-page screenshot; dims stored below
            object_key=key,
            thumb_key=None,
            image_hash=image_hash,
            width=0,
            height=0,
            status="active",
        )
        db.add(b)
        created.append({"url": url, "browser": browser, "image_hash": image_hash})

    db.commit()
    return {"project_id": str(proj.id), "branch": branch, "baselines_created": len(created), "baselines": created}


def url_hash(url: str) -> str:
    import hashlib

    return hashlib.sha256(url.encode()).hexdigest()[:16]


@router.get("/{project_id}/baselines")
async def list_baselines(
    project_id: str,
    branch: str | None = None,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    proj = _get_owned_project(db, project_id, user)
    q = db.query(VisualBaseline).filter(VisualBaseline.project_id == proj.id)
    if branch:
        q = q.filter(VisualBaseline.branch == branch)
    rows = q.order_by(VisualBaseline.branch, VisualBaseline.url).all()
    return {
        "baselines": [
            {
                "id": str(b.id),
                "branch": b.branch,
                "url": b.url,
                "browser": b.browser,
                "image_hash": b.image_hash,
                "status": b.status,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in rows
        ],
        "total": len(rows),
    }


# ── compare ────────────────────────────────────────────────────────────────


@router.post("/{project_id}/compare", status_code=201)
async def compare_job(
    project_id: str,
    req: CompareRequest,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Diff a completed job's screenshots against the active baselines."""
    from src.analysis.baseline import compare_images, render_diff_image
    from src.api.app import _get_owned_job

    proj = _get_owned_project(db, project_id, user)
    job = _get_owned_job(db, req.job_id, user)
    branch = req.branch or proj.default_branch

    snap_dir = _job_snapshot_dir(job)
    if not snap_dir:
        raise HTTPException(status_code=400, detail="Job has no snapshot directory")

    pairs = _enumerate_job_snapshots(db, job)
    results: list[dict[str, Any]] = []
    regressions = matches = new_baselines = 0

    for url, browser in pairs:
        baseline = _active_baseline(db, proj, branch, url, browser)
        filename = snapshot_filename_for(url, browser)
        current_bytes = await asyncio.to_thread(_read_snapshot_bytes, snap_dir, filename)

        if baseline is None:
            if current_bytes is not None:
                new_baselines += 1
            results.append({"url": url, "browser": browser, "verdict": "new_baseline"})
            continue
        if current_bytes is None:
            results.append({"url": url, "browser": browser, "verdict": "missing_current"})
            continue

        baseline_bytes = await asyncio.to_thread(s3.get_bytes, baseline.object_key)
        res = await asyncio.to_thread(
            compare_images,
            baseline_bytes,
            current_bytes,
            url=url,
            threshold=proj.threshold,
            ignore_regions=proj.ignore_regions,
        )

        entry = res.to_dict()
        entry["browser"] = browser

        if res.verdict == "regression":
            regressions += 1
            diff_key = await asyncio.to_thread(
                _store_image, proj, branch, "diff", f"{url_hash(url)}_{browser}.png",
                await asyncio.to_thread(
                    render_diff_image, baseline_bytes, current_bytes, proj.ignore_regions
                ),
            )
            cur_key = await asyncio.to_thread(
                _store_image, proj, branch, "current", f"{url_hash(url)}_{browser}.png", current_bytes
            )
            entry["diff_key"] = diff_key
            entry["current_key"] = cur_key
            entry["baseline_key"] = baseline.object_key
        elif res.verdict == "match":
            matches += 1

        results.append(entry)

    vc = VisualCompare(
        project_id=proj.id,
        job_id=job.id,
        branch=branch,
        results=results,
        regressions=regressions,
        matches=matches,
        new_baselines=new_baselines,
        status="completed",
    )
    db.add(vc)
    db.commit()
    db.refresh(vc)

    return {
        "compare_id": str(vc.id),
        "project_id": str(proj.id),
        "branch": branch,
        "job_id": req.job_id,
        "regressions": regressions,
        "matches": matches,
        "new_baselines": new_baselines,
        "total": len(results),
        "results": results,
    }


@router.get("/{project_id}/compare/{compare_id}")
async def get_compare(
    project_id: str,
    compare_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    proj = _get_owned_project(db, project_id, user)
    try:
        cid = uuid.UUID(compare_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Compare not found") from None
    vc = db.query(VisualCompare).filter_by(id=cid, project_id=proj.id).first()
    if not vc:
        raise HTTPException(status_code=404, detail="Compare not found")
    return {
        "compare_id": str(vc.id),
        "project_id": str(vc.project_id),
        "branch": vc.branch,
        "job_id": str(vc.job_id) if vc.job_id else None,
        "regressions": vc.regressions,
        "matches": vc.matches,
        "new_baselines": vc.new_baselines,
        "status": vc.status,
        "created_at": vc.created_at.isoformat() if vc.created_at else None,
        "results": vc.results,
    }


@router.post("/{project_id}/compare/{compare_id}/approve")
async def approve_compare(
    project_id: str,
    compare_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Promote the compare's current screenshots to be the new baselines.

    Only 'regression' entries have a persisted current image; approve turns
    those into the active baseline (superseding the old one).
    """
    proj = _get_owned_project(db, project_id, user)
    try:
        cid = uuid.UUID(compare_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Compare not found") from None
    vc = db.query(VisualCompare).filter_by(id=cid, project_id=proj.id).first()
    if not vc:
        raise HTTPException(status_code=404, detail="Compare not found")

    promoted = 0
    for entry in vc.results or []:
        if entry.get("verdict") != "regression" or not entry.get("current_key"):
            continue
        url = entry["url"]
        browser = entry["browser"]
        cur_key = entry["current_key"]
        _supersede_baselines(db, proj, vc.branch, url, browser)
        db.add(
            VisualBaseline(
                project_id=proj.id,
                branch=vc.branch,
                url=url,
                browser=browser,
                viewport={"width": 0, "height": 0},
                object_key=cur_key,
                thumb_key=None,
                image_hash="",
                width=entry.get("width", 0),
                height=entry.get("height", 0),
                status="active",
            )
        )
        promoted += 1

    db.commit()
    return {"status": "approved", "promoted": promoted}


# ── artifact proxy ─────────────────────────────────────────────────────────


@router.get("/{project_id}/artifact/{key:path}")
async def serve_artifact(
    project_id: str,
    key: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> Response:
    """Stream an S3 artifact, but only if its key is under this project's tenant prefix."""
    proj = _get_owned_project(db, project_id, user)
    prefix = f"tenants/{owner_namespace(proj.owner_id)}/projects/{proj.id}/"
    if not key.startswith(prefix) or ".." in key:
        raise HTTPException(status_code=404, detail="Artifact not found")
    try:
        data = await asyncio.to_thread(s3.get_bytes, key)
    except Exception as e:
        logger.warning("artifact fetch failed %s: %s", key, e)
        raise HTTPException(status_code=404, detail="Artifact not found") from None
    return Response(content=data, media_type="image/png")
