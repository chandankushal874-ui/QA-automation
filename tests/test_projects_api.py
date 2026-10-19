"""Tests for the projects + visual-baseline diff API.

Covers: CRUD, owner isolation (anti-enumeration 404), and the full
capture-baseline → compare → approve lifecycle against synthetic snapshots and
a fake S3 client (no live MinIO).
"""

from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient
from PIL import Image

from src.api.app import app
from src.core import s3
from src.models.database import Base, Job, JobStatus, PlatformUser, Project

Base.metadata.create_all(bind=__import__("src.api.app", fromlist=["engine"]).engine)

client = TestClient(app)


# ── fake S3 ─────────────────────────────────────────────────────────────────


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType=""):
        self.objects[Key] = Body
        return {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _client_error("NoSuchKey")
        return {"Body": _FakeBody(self.objects[Key])}

    def head_bucket(self, Bucket):
        return {}

    def create_bucket(self, Bucket):
        return {}


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def _client_error(code: str) -> Exception:
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


# ── helpers ─────────────────────────────────────────────────────────────────


def _ensure_user(username: str, password: str) -> None:
    from src.api.app import SessionLocal
    from src.api.auth import hash_password

    db = SessionLocal()
    try:
        if not db.query(PlatformUser).filter(PlatformUser.username == username).first():
            db.add(
                PlatformUser(
                    id=uuid.uuid4(), username=username, email=f"{username}@localhost",
                    password_hash=hash_password(password), role="user", is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()


def _login(username: str, password: str) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _png(color: tuple[int, int, int] = (10, 10, 10), size=(40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _make_job(owner_id: str, snap_dir: str, url: str) -> str:
    """Insert a completed Job with one CrawlState + snapshot on disk."""
    from src.api.app import SessionLocal
    from src.models.database import CrawlState, CrawlStateStatus

    db = SessionLocal()
    try:
        job = Job(
            id=uuid.uuid4(), owner_id=uuid.UUID(owner_id), url=url,
            status=JobStatus.COMPLETED,
            browser_matrix={"chromium": True, "firefox": False, "webkit": False},
            config={"isolation": {"paths": {"snapshots": snap_dir}}},
        )
        db.add(job)
        db.flush()
        db.add(
            CrawlState(
                job_id=job.id, url=url, method="GET", depth=0,
                status=CrawlStateStatus.VISITED, visit_count=1,
            )
        )
        db.commit()
        return str(job.id)
    finally:
        db.close()


def _write_snapshot(snap_dir: str, filename: str, data: bytes) -> None:
    import os

    os.makedirs(snap_dir, exist_ok=True)
    with open(os.path.join(snap_dir, filename), "wb") as fh:
        fh.write(data)


# ── setup ──────────────────────────────────────────────────────────────────

_ensure_user("proj_a", "pw")
_ensure_user("proj_b", "pw")

_s3 = FakeS3()
s3._inject_client(_s3)  # type: ignore[attr-defined]


def test_project_crud_and_isolation() -> None:
    h = {"Authorization": f"Bearer {_login('proj_a', 'pw')['token']}"}
    hb = {"Authorization": f"Bearer {_login('proj_b', 'pw')['token']}"}
    hc = {**h, "Content-Type": "application/json"}

    # create
    r = client.post("/api/projects", headers=hc, json={"name": "Storefront", "base_url": "https://shop.example.com"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["slug"] == "storefront"

    # duplicate slug → 409
    r = client.post("/api/projects", headers=hc, json={"name": "Storefront", "base_url": "https://x.example.com"})
    assert r.status_code == 409

    # list as owner
    r = client.get("/api/projects", headers=h)
    assert any(p["id"] == pid for p in r.json()["projects"])

    # isolation: user B cannot see or read it (404 anti-enumeration)
    r = client.get("/api/projects", headers=hb)
    assert all(p["id"] != pid for p in r.json()["projects"])
    r = client.get(f"/api/projects/{pid}", headers=hb)
    assert r.status_code == 404

    # update
    r = client.patch(f"/api/projects/{pid}", headers=hc, json={"threshold": 0.5, "ignore_regions": [{"x": 0, "y": 0, "width": 10, "height": 10}]})
    assert r.status_code == 200
    assert r.json()["threshold"] == 0.5
    assert r.json()["ignore_regions"] == [{"x": 0, "y": 0, "width": 10, "height": 10}]

    # user B cannot delete
    r = client.delete(f"/api/projects/{pid}", headers=hb)
    assert r.status_code == 404
    # owner can
    r = client.delete(f"/api/projects/{pid}", headers=h)
    assert r.status_code == 200


def test_capture_compare_approve_lifecycle() -> None:
    import tempfile

    h = {"Authorization": f"Bearer {_login('proj_a', 'pw')['token']}"}
    hc = {**h, "Content-Type": "application/json"}

    r = client.post("/api/projects", headers=hc, json={"name": "Lifecycle", "base_url": "https://l.example.com", "threshold": 0.1})
    pid = r.json()["id"]

    tmp = tempfile.mkdtemp(prefix="cbtp_proj_")
    url = "https://l.example.com/home"

    # baseline job (gray image) + branch job (white image → regression)
    job_base = _make_job(_uid("proj_a"), tmp, url)
    job_pr = _make_job(_uid("proj_a"), tmp, url)

    from src.api.projects import snapshot_filename_for

    fn = snapshot_filename_for(url, "chromium")
    _write_snapshot(tmp, fn, _png((50, 50, 50)))

    # capture baseline
    r = client.post(f"/api/projects/{pid}/baseline", headers=hc, json={"job_id": job_base})
    assert r.status_code == 201, r.text
    assert r.json()["baselines_created"] == 1

    # list baselines
    r = client.get(f"/api/projects/{pid}/baselines", headers=h)
    assert r.json()["total"] == 1

    # compare against a *changed* snapshot (overwrite to white)
    _write_snapshot(tmp, fn, _png((250, 250, 250)))
    r = client.post(f"/api/projects/{pid}/compare", headers=hc, json={"job_id": job_pr})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["regressions"] == 1
    cid = body["compare_id"]

    # get compare
    r = client.get(f"/api/projects/{pid}/compare/{cid}", headers=h)
    assert r.status_code == 200
    assert r.json()["regressions"] == 1

    # approve → promotes current to new baseline (now 2 baseline rows: 1 active)
    r = client.post(f"/api/projects/{pid}/compare/{cid}/approve", headers=h)
    assert r.status_code == 200
    assert r.json()["promoted"] == 1

    # cleanup
    from src.api.app import SessionLocal
    from src.models.database import CrawlState, VisualBaseline, VisualCompare

    db = SessionLocal()
    try:
        db.query(CrawlState).filter_by(job_id=uuid.UUID(job_base)).delete()
        db.query(CrawlState).filter_by(job_id=uuid.UUID(job_pr)).delete()
        db.query(Job).filter(Job.id.in_([uuid.UUID(job_base), uuid.UUID(job_pr)])).delete()
        db.query(VisualBaseline).filter_by(project_id=uuid.UUID(pid)).delete()
        db.query(VisualCompare).filter_by(project_id=uuid.UUID(pid)).delete()
        db.query(Project).filter_by(id=uuid.UUID(pid)).delete()
        db.commit()
    finally:
        db.close()


def test_compare_with_no_baseline_returns_new_baseline() -> None:
    import tempfile

    h = {"Authorization": f"Bearer {_login('proj_a', 'pw')['token']}"}
    hc = {**h, "Content-Type": "application/json"}

    r = client.post("/api/projects", headers=hc, json={"name": "NoBase", "base_url": "https://nb.example.com"})
    pid = r.json()["id"]
    tmp = tempfile.mkdtemp(prefix="cbtp_nb_")
    url = "https://nb.example.com/page"
    job = _make_job(_uid("proj_a"), tmp, url)
    from src.api.projects import snapshot_filename_for

    _write_snapshot(tmp, snapshot_filename_for(url, "chromium"), _png())

    r = client.post(f"/api/projects/{pid}/compare", headers=hc, json={"job_id": job})
    assert r.status_code == 201, r.text
    assert r.json()["new_baselines"] == 1
    assert r.json()["regressions"] == 0

    # cleanup
    from src.api.app import SessionLocal
    from src.models.database import CrawlState, VisualCompare

    db = SessionLocal()
    try:
        db.query(CrawlState).filter_by(job_id=uuid.UUID(job)).delete()
        db.query(Job).filter_by(id=uuid.UUID(job)).delete()
        db.query(VisualCompare).filter_by(project_id=uuid.UUID(pid)).delete()
        db.query(Project).filter_by(id=uuid.UUID(pid)).delete()
        db.commit()
    finally:
        db.close()


def _uid(username: str) -> str:
    from src.api.app import SessionLocal

    db = SessionLocal()
    try:
        u = db.query(PlatformUser).filter_by(username=username).first()
        return str(u.id)
    finally:
        db.close()
