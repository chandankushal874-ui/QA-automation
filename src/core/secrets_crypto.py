"""Encrypt at rest for target-site credentials (AuthProfile passwords).

Uses Fernet when cryptography is installed; falls back to reversible
HMAC-bound base64 token so existing deployments still run without a new
pip dependency (weaker but better than raw plaintext).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)

_PREFIX_FERNET = "enc:v1:"
_PREFIX_LEGACY = "enc:v0:"


def _secret_material() -> bytes:
    raw = settings.jwt_secret or settings.admin_password or "cbtp-self-hosted-change-me"
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _fernet() -> Any | None:
    try:
        from cryptography.fernet import Fernet

        key = base64.urlsafe_b64encode(_secret_material())
        return Fernet(key)
    except Exception:
        return None


def encrypt_secret(plaintext: str | None) -> str:
    """Encrypt a secret string for DB storage. Empty stays empty."""
    if not plaintext:
        return ""
    f = _fernet()
    if f is not None:
        token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{_PREFIX_FERNET}{token}"
    # Fallback: xor-style with keystream (not Fernet-grade; still not plaintext)
    key = _secret_material()
    data = plaintext.encode("utf-8")
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    mac = hmac.new(key, out, hashlib.sha256).hexdigest()[:16]
    blob = base64.urlsafe_b64encode(out).decode("ascii")
    return f"{_PREFIX_LEGACY}{mac}:{blob}"


def decrypt_secret(stored: str | None) -> str:
    """Decrypt a stored secret. Plaintext legacy values returned as-is."""
    if not stored:
        return ""
    if stored.startswith(_PREFIX_FERNET):
        f = _fernet()
        if f is None:
            logger.warning("Encrypted secret present but cryptography unavailable")
            return ""
        try:
            return f.decrypt(stored[len(_PREFIX_FERNET) :].encode("ascii")).decode("utf-8")
        except Exception as e:
            logger.warning("Fernet decrypt failed: %s", e)
            return ""
    if stored.startswith(_PREFIX_LEGACY):
        try:
            rest = stored[len(_PREFIX_LEGACY) :]
            mac, blob = rest.split(":", 1)
            key = _secret_material()
            raw = base64.urlsafe_b64decode(blob.encode("ascii"))
            expect = hmac.new(key, raw, hashlib.sha256).hexdigest()[:16]
            if not hmac.compare_digest(mac, expect):
                return ""
            data = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
            return data.decode("utf-8")
        except Exception as e:
            logger.warning("Legacy decrypt failed: %s", e)
            return ""
    # Legacy plaintext row
    return stored
