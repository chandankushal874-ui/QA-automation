"""Tests for the S3/MinIO storage layer (src/core/s3.py).

Uses a tiny in-memory fake boto3 client — no live MinIO, no moto dependency.
"""

from __future__ import annotations

from typing import Any

from src.core import s3


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.buckets: set[str] = set()
        self.created: list[str] = []
        self.last_urls: list[str] = []

    def head_bucket(self, Bucket: str) -> dict[str, Any]:
        if Bucket not in self.buckets:
            raise _client_error("NoSuchBucket")
        return {}

    def create_bucket(self, Bucket: str) -> dict[str, Any]:
        if Bucket in self.buckets:
            raise _client_error("BucketAlreadyOwnedByYou")
        self.buckets.add(Bucket)
        self.created.append(Bucket)
        return {}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str = "") -> dict[str, Any]:
        self.objects[Key] = Body
        return {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise _client_error("NoSuchKey")
        return {"Body": FakeBody(self.objects[Key])}

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise _client_error("404")
        return {}

    def generate_presigned_url(self, _op: str, Params: dict[str, Any], ExpiresIn: int) -> str:
        url = f"https://fake/{Params['Bucket']}/{Params['Key']}?exp={ExpiresIn}"
        self.last_urls.append(url)
        return url


def _client_error(code: str) -> Exception:
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


def _inject(client: FakeS3Client) -> None:
    s3._inject_client(client)  # type: ignore[attr-defined]
    s3.reset_bucket_flag()


def test_bootstrap_and_put_get() -> None:
    c = FakeS3Client()
    _inject(c)
    name = s3.ensure_bucket(client=c)
    assert name == "cbtp-artifacts"
    assert name in c.buckets

    key = "tenants/ns/projects/p1/baselines/main/baseline/a.png"
    s3.put_bytes(key, b"\x89PNG", client=c)
    assert s3.exists(key, client=c)
    assert s3.get_bytes(key, client=c) == b"\x89PNG"


def test_ensure_bucket_idempotent() -> None:
    c = FakeS3Client()
    _inject(c)
    s3.ensure_bucket(client=c)
    s3.ensure_bucket(client=c)
    # head_bucket now succeeds → create_bucket not called twice
    assert c.created == ["cbtp-artifacts"]


def test_object_key_tenant_namespaced() -> None:
    key = s3.object_key("owner-uuid", "proj-uuid", "main", "diff", "diff_1.png")
    assert key.startswith("tenants/")
    assert "/projects/proj-uuid/baselines/main/diff/diff_1.png" in key
    # Two different owners must never share a prefix.
    k1 = s3.baseline_prefix("aaaa", "p", "main")
    k2 = s3.baseline_prefix("bbbb", "p", "main")
    assert k1 != k2


def test_presigned_url() -> None:
    c = FakeS3Client()
    _inject(c)
    s3.ensure_bucket(client=c)
    key = "tenants/ns/x"
    s3.put_bytes(key, b"x", client=c)
    url = s3.presigned_get_url(key, expires_in=60, client=c)
    assert url.startswith("https://fake/") and "exp=60" in url


def test_sha256_stable() -> None:
    assert s3.sha256_bytes(b"abc") == s3.sha256_bytes(b"abc")
    assert len(s3.sha256_bytes(b"abc")) == 64
