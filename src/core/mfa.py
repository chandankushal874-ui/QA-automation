"""TOTP multi-factor authentication (Phase 2).

Uses pyotp (already in project deps). Secrets are stored per-user on PlatformUser.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)


def generate_mfa_secret() -> str:
    import pyotp

    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str, issuer: str = "CBTP") -> str:
    import pyotp

    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name=issuer)


def verify_totp(secret: str, code: str, *, valid_window: int = 1) -> bool:
    if not secret or not code:
        return False
    try:
        import pyotp

        totp = pyotp.TOTP(secret)
        return bool(totp.verify(str(code).strip().replace(" ", ""), valid_window=valid_window))
    except Exception as e:
        logger.warning("TOTP verify error: %s", e)
        return False


def qr_data_url_hint(uri: str) -> str:
    """Return a URL that renders a QR (no local image dep). Clients may also use otpauth URI."""
    return f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={quote(uri, safe='')}"


def mfa_status_dict(user: Any) -> dict[str, Any]:
    try:
        from src.config import settings

        available = bool(getattr(settings, "mfa_enabled", True))
    except Exception:
        available = True
    return {
        "mfaEnabled": bool(getattr(user, "mfa_enabled", False)) and available,
        "mfaFeatureAvailable": available,
        "authProvider": getattr(user, "auth_provider", None) or "local",
    }
