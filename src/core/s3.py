"""Thin S3/MinIO object-storage layer for crawl artifacts (baselines, diffs).

boto3 is already a runtime dependency; MinIO is wired in docker-compose.prod.yml
(S3_ENDPOINT_URL=http://minio:9000) but had no client in code. This module
provides the missing glue:

  - lazy, process-safe client construction
  - idempotent bucket bootstrap
  - put/get bytes and presigned GET URLs
  - tenant-namespaced object keys (never cross-owner)

All methods are synchronous (boto3 is); async callers wrap them in
``asyncio.to_thread``. The client is injectable so tests can substitute a fake
without hitting a live endpoint.

Object key layout (stable contract, do not change without a migration):
    tenants/{owner_namespace}/projects/{project_id}/baselines/{branch}/{kind}/{name}
where kind is one of ``baseline``, ``current``, ``diff``, ``thumb``.
"""

from __future__ import annotations

import hashlib
import io
import logging
import threading
from typing import Any, BinaryIO

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError

from src.core.tenant_isolation import owner_namespace

logger = logging.getLogger(__name__)

_CONTENT_TYPE_PNG = "image/png"
_CONTENT_TYPE_JPEG = "image/jpeg"

_lock = threading.Lock()
_client: BaseClient | None = None


def _botocore_config_kwargs() -> dict[str, Any]:
    try:
        from botocore.config import Config

        return {"config": Config(signature_version="s3v4", s3={"addressing_style": "path"})}
    except ImportError:
        return {}


def get_client() -> BaseClient:
    """Return the shared S3 client, constructing it once (thread-safe)."""
    global _client
    with _lock:
        if _client is None:
            from src.config import settings

            _client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                region_name="us-east-1",
                **_botocore_config_kwargs(),
            )
        return _client


def _inject_client(client: BaseClient) -> None:
    """Test hook: replace the shared client with a fake."""
    global _client
    with _lock:
        _client = client


def reset_client() -> None:
    """Clear the cached client (tests / settings reload)."""
    global _client
    with _lock:
        _client = None


def bucket_name() -> str:
    from src.config import settings

    return settings.s3_bucket_name


def ensure_bucket(client: BaseClient | None = None) -> str:
    """Idempotently create the artifact bucket. Returns the bucket name."""
    name = bucket_name()
    c = client or get_client()
    try:
        c.head_bucket(Bucket=name)
    except (ClientError, BotoCoreError):
        # MinIO raises on head_bucket when missing; create it (region required
        # by AWS S3 for us-east-1 create-bucket calls is tolerated by MinIO).
        try:
            c.create_bucket(Bucket=name)
        except (ClientError, BotoCoreError) as e:
            # BucketAlreadyOwnedByYou / already-exists race — safe to ignore.
            if "BucketAlreadyOwnedByYou" in str(e) or "BucketAlreadyExists" in str(e):
                pass
            else:
                logger.warning("ensure_bucket create failed: %s", e)
    return name


_bucket_ready = False


def ensure_bucket_once(client: BaseClient | None = None) -> str:
    """Bootstrap the bucket once per process; subsequent calls are no-ops."""
    global _bucket_ready
    if not _bucket_ready:
        ensure_bucket(client)
        _bucket_ready = True
    return bucket_name()


def reset_bucket_flag() -> None:
    """Test hook: force the next ensure_bucket_once to re-bootstrap."""
    global _bucket_ready
    _bucket_ready = False


def baseline_prefix(owner_id: Any, project_id: Any, branch: str) -> str:
    """Tenant-namespaced prefix for a project+branch's baseline objects."""
    ns = owner_namespace(owner_id)
    return f"tenants/{ns}/projects/{project_id}/baselines/{branch}"


def object_key(
    owner_id: Any,
    project_id: Any,
    branch: str,
    kind: str,
    name: str,
) -> str:
    """Compose a fully-qualified object key for one artifact."""
    return f"{baseline_prefix(owner_id, project_id, branch)}/{kind}/{name}"


def put_bytes(
    key: str,
    data: bytes,
    content_type: str = _CONTENT_TYPE_PNG,
    client: BaseClient | None = None,
) -> None:
    """Upload raw bytes under key (idempotent overwrite). Bootstraps the bucket
    lazily on first use so deployments with a fresh MinIO just work."""
    c = client or get_client()
    ensure_bucket_once(c)
    c.put_object(Bucket=bucket_name(), Key=key, Body=data, ContentType=content_type)


def put_file(
    key: str,
    path: str,
    content_type: str = _CONTENT_TYPE_PNG,
    client: BaseClient | None = None,
) -> None:
    with open(path, "rb") as fh:
        put_bytes(key, fh.read(), content_type=content_type, client=client)


def get_bytes(key: str, client: BaseClient | None = None) -> bytes:
    """Fetch an object's bytes; raises KeyError-style if missing."""
    c = client or get_client()
    obj = c.get_object(Bucket=bucket_name(), Key=key)
    return obj["Body"].read()


def exists(key: str, client: BaseClient | None = None) -> bool:
    c = client or get_client()
    try:
        c.head_object(Bucket=bucket_name(), Key=key)
        return True
    except (ClientError, BotoCoreError):
        return False


def presigned_get_url(key: str, expires_in: int = 900, client: BaseClient | None = None) -> str:
    """Return a time-limited public URL for an object (default 15 min)."""
    c = client or get_client()
    return c.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name(), "Key": key},
        ExpiresIn=expires_in,
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bytes_to_png(data: bytes) -> bytes:
    """Normalize raw bytes to PNG (decode → re-encode) for canonical storage.

    Kept as a helper so diff/baseline code can canonicalize before hashing.
    """
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def to_png_fileobj(data: bytes) -> BinaryIO:
    return io.BytesIO(data)


__all__ = [
    "baseline_prefix",
    "bucket_name",
    "ensure_bucket",
    "ensure_bucket_once",
    "exists",
    "get_bytes",
    "get_client",
    "object_key",
    "presigned_get_url",
    "put_bytes",
    "put_file",
    "reset_bucket_flag",
    "reset_client",
    "sha256_bytes",
]
