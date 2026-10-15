"""API tokens for CI/CD and programmatic access.

Design (production-grade, additive — no changes to existing auth flows):
- Tokens are stored SHA-256-hashed; the raw value is shown exactly once.
- Format: cbtp_<32 hex chars>  (prefix enables future secret-scanning)
- Auth precedence in get_current_user: JWT Bearer → API token Bearer.
- Tokens inherit the owner's role and job isolation; revocation = is_active flip.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.auth import get_current_user, get_db_dep
from src.models.database import ApiToken, PlatformUser

router = APIRouter(prefix="/api/me/tokens", tags=["api-tokens"])

TOKEN_PREFIX = "cbtp_"


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_api_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). Raw is shown once at creation."""
    raw = f"{TOKEN_PREFIX}{secrets.token_hex(16)}"
    return raw, _hash_token(raw)


def resolve_api_token(raw: str) -> str | None:
    """Validate format, return hash or None."""
    if not raw.startswith(TOKEN_PREFIX) or len(raw) != len(TOKEN_PREFIX) + 32:
        return None
    return _hash_token(raw)


def _token_dict(t: ApiToken) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "name": t.name,
        "created_at": t.created_at.isoformat(),
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        "is_active": t.is_active,
    }


class CreateTokenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Label, e.g. 'github-actions'")


class CreateTokenResponse(BaseModel):
    token: str
    record: dict[str, Any]


@router.post("", response_model=CreateTokenResponse, status_code=201)
async def create_token(
    req: CreateTokenRequest,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> CreateTokenResponse:
    """Create an API token for the current user (CI/CD use).

    The raw token is returned once and never stored server-side.
    """
    raw, token_hash = generate_api_token()
    record = ApiToken(
        user_id=user.id,
        name=req.name.strip(),
        token_hash=token_hash,
        is_active=True,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return CreateTokenResponse(token=raw, record=_token_dict(record))


@router.get("")
async def list_tokens(
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """List the current user's tokens (hashes never exposed)."""
    tokens = (
        db.query(ApiToken)
        .filter(ApiToken.user_id == user.id)
        .order_by(ApiToken.created_at.desc())
        .all()
    )
    return {"tokens": [_token_dict(t) for t in tokens], "total": len(tokens)}


@router.delete("/{token_id}")
async def revoke_token(
    token_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Revoke one of the current user's tokens."""
    import uuid as _uuid

    try:
        tid = _uuid.UUID(token_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid token id") from None

    record = (
        db.query(ApiToken)
        .filter(ApiToken.id == tid, ApiToken.user_id == user.id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Token not found")
    record.is_active = False
    db.commit()
    return {"status": "revoked", "token_id": token_id}


def authenticate_api_token(db: Session, raw: str) -> PlatformUser | None:
    """Resolve an API token to its owner (active tokens only).

    Updates last_used_at at most once per hour per token (avoids a DB write
    on every CI request — write amplification under 100+ uses).

    Called from auth.get_current_user only when the bearer is not a valid JWT.
    Never raises — returning None lets the caller raise a single 401.
    """
    token_hash = resolve_api_token(raw)
    if token_hash is None:
        return None
    record = (
        db.query(ApiToken)
        .filter(ApiToken.token_hash == token_hash, ApiToken.is_active == True)  # noqa: E712
        .first()
    )
    if not record:
        return None
    user = (
        db.query(PlatformUser)
        .filter(PlatformUser.id == record.user_id, PlatformUser.is_active == True)  # noqa: E712
        .first()
    )
    if not user:
        return None

    # Throttled usage stamp: max one write per token per hour.
    # SQLite returns naive datetimes while we compare against UTC-aware now
    # ("can't subtract offset-naive and offset-aware datetimes") — normalize.
    now = datetime.now(UTC)
    last = record.last_used_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if last is None or (now - last).total_seconds() >= 3600:
        record.last_used_at = now
        try:
            db.commit()
        except Exception:
            db.rollback()
    return user
