"""True multi-user isolation: User A crawler/data never collides with User B."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.api.auth import hash_password, user_can_access_job
from src.core.frontier import FrontierNode, RedisFrontier
from src.core.mock_redis import MockRedis
from src.core.tenant_isolation import (
    apply_job_isolation_paths,
    assert_job_owner_isolation,
    crawl_queue_name,
    frontier_key_prefix,
    owner_namespace,
    owners_match,
    resolve_job_owner_id,
    safe_resolve_under_tenant,
    tenant_root,
)
from src.models.database import PlatformUser, UserRole


def test_owner_namespaces_differ_for_two_users():
    a = uuid.uuid4()
    b = uuid.uuid4()
    assert owner_namespace(a) != owner_namespace(b)
    assert owner_namespace(a) == owner_namespace(str(a))
    assert owner_namespace(None) == "legacy"


def test_frontier_keys_never_collide_across_users():
    a = uuid.uuid4()
    b = uuid.uuid4()
    job = uuid.uuid4()
    # Same job id, different owners → different Redis key prefixes
    assert frontier_key_prefix(a, job) != frontier_key_prefix(b, job)
    assert owner_namespace(a) in frontier_key_prefix(a, job)
    assert owner_namespace(b) in frontier_key_prefix(b, job)


def test_disk_roots_are_per_user():
    a = uuid.uuid4()
    b = uuid.uuid4()
    assert tenant_root(a) != tenant_root(b)
    assert str(owner_namespace(a)) in str(tenant_root(a))


def test_redis_frontier_isolation_with_mock_redis():
    """Push on User A frontier must not appear in User B frontier."""
    redis = MockRedis()
    owner_a = str(uuid.uuid4())
    owner_b = str(uuid.uuid4())
    job_a = str(uuid.uuid4())
    job_b = str(uuid.uuid4())

    fa = RedisFrontier(redis, job_id=job_a, owner_id=owner_a, max_size=100)
    fb = RedisFrontier(redis, job_id=job_b, owner_id=owner_b, max_size=100)

    fa.push(FrontierNode(url="https://a.example/secret", priority_score=0.1))
    assert fa.size() == 1
    assert fb.size() == 0
    assert fb.pop_min() is None

    node = fa.pop_min()
    assert node is not None
    assert node.url == "https://a.example/secret"
    assert fa.size() == 0


def test_same_job_id_different_owners_still_isolated():
    """Even if job UUIDs collided (shouldn't), owner prefix keeps keyspaces apart."""
    redis = MockRedis()
    owner_a = str(uuid.uuid4())
    owner_b = str(uuid.uuid4())
    shared_job = str(uuid.uuid4())

    fa = RedisFrontier(redis, job_id=shared_job, owner_id=owner_a)
    fb = RedisFrontier(redis, job_id=shared_job, owner_id=owner_b)

    fa.push(FrontierNode(url="https://only-a.example/", priority_score=0.0))
    assert fa.size() == 1
    assert fb.size() == 0


def test_user_can_access_job_owner_only():
    user_a = PlatformUser(
        id=uuid.uuid4(),
        username="usera",
        email="a@local",
        password_hash=hash_password("pass-a"),
        role=UserRole.USER.value,
        is_active=True,
    )
    user_b = PlatformUser(
        id=uuid.uuid4(),
        username="userb",
        email="b@local",
        password_hash=hash_password("pass-b"),
        role=UserRole.USER.value,
        is_active=True,
    )
    admin = PlatformUser(
        id=uuid.uuid4(),
        username="admin",
        email="admin@local",
        password_hash=hash_password("admin"),
        role=UserRole.ADMIN.value,
        is_active=True,
    )
    job_a = SimpleNamespace(owner_id=user_a.id, id=uuid.uuid4())
    job_b = SimpleNamespace(owner_id=user_b.id, id=uuid.uuid4())
    legacy = SimpleNamespace(owner_id=None, id=uuid.uuid4())

    assert user_can_access_job(user_a, job_a) is True
    assert user_can_access_job(user_a, job_b) is False
    assert user_can_access_job(user_b, job_a) is False
    assert user_can_access_job(user_a, legacy) is False
    assert user_can_access_job(admin, job_a) is True
    assert user_can_access_job(admin, job_b) is True
    assert user_can_access_job(admin, legacy) is True


def test_apply_job_isolation_paths_stamps_owner():
    owner = uuid.uuid4()
    job = SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=owner,
        url="https://example.com",
        config={},
    )
    paths = apply_job_isolation_paths(job)
    assert "jobs" in paths["job_root"]
    assert str(owner_namespace(owner)) in paths["tenant_root"]
    assert job.config["isolation"]["owner_id"] == str(owner)
    assert job.config["isolation"]["tenant"] == owner_namespace(owner)
    assert job.config["isolation"]["level"] == "user_id"


def test_assert_job_owner_isolation_detects_mismatch():
    job = SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        config={"isolation": {"owner_id": str(uuid.uuid4())}},
    )
    with pytest.raises(ValueError, match="Isolation breach"):
        assert_job_owner_isolation(job)


def test_resolve_job_owner_id_prefers_column():
    oid = uuid.uuid4()
    job = SimpleNamespace(owner_id=oid, config={"isolation": {"owner_id": str(uuid.uuid4())}})
    assert resolve_job_owner_id(job) == str(oid)


def test_owners_match():
    a = uuid.uuid4()
    assert owners_match(a, str(a)) is True
    assert owners_match(a, uuid.uuid4()) is False
    assert owners_match(None, a) is False


def test_crawl_queue_name_is_per_user():
    a = uuid.uuid4()
    b = uuid.uuid4()
    assert crawl_queue_name(a) != crawl_queue_name(b)
    assert crawl_queue_name(a).startswith("crawl:")
    assert crawl_queue_name(None) == "default"


def test_safe_resolve_blocks_path_traversal(tmp_path, monkeypatch):
    owner = uuid.uuid4()
    # Point tenant base at tmp
    monkeypatch.chdir(tmp_path)
    root = tenant_root(owner)
    (root / "jobs" / "x").mkdir(parents=True)
    secret = root / "jobs" / "x" / "ok.png"
    secret.write_bytes(b"png")

    ok = safe_resolve_under_tenant(owner, "jobs/x/ok.png", also_allow_legacy_reports=False)
    assert ok is not None

    # Traversal attempt
    assert safe_resolve_under_tenant(owner, "../etc/passwd", also_allow_legacy_reports=False) is None
    assert safe_resolve_under_tenant(owner, "/etc/passwd", also_allow_legacy_reports=False) is None

    # Other tenant's file
    other = uuid.uuid4()
    assert safe_resolve_under_tenant(other, "jobs/x/ok.png", also_allow_legacy_reports=False) is None
