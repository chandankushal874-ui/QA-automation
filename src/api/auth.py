"""
Simple multi-user auth — username + password only.

Rules:
- Every account is stored in platform_users (unique username).
- Login requires username AND password (no password-only / hardcoded guest logins).
- Register creates a new unique user; duplicate usernames are rejected.
- JWT sub = user.id → every job is stamped owner_id → full crawl isolation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from base64 import urlsafe_b64encode
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from src.config import settings
from src.models.database import PlatformUser, UserRole

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_SECRET = settings.jwt_secret or settings.admin_password or "cbtp-self-hosted-change-me"
_ADMIN_PASSWORD = settings.admin_password or "admin123"
_ADMIN_USERNAME = (settings.admin_username or "admin").strip().lower()
_PBKDF2_ITERS = 120_000
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


def _b64(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64_decode(s: str) -> bytes:
    import base64

    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _sign(data: str) -> str:
    return hmac.new(_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()


def hash_password(password: str, salt: str | None = None) -> str:
    """Return salt$hash (PBKDF2-SHA256)."""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return hmac.compare_digest(password, stored or "")
    salt, _hex = stored.split("$", 1)
    return hmac.compare_digest(hash_password(password, salt), stored)


def create_token(payload: dict[str, Any]) -> str:
    """Create a simple JWT-like token (default 7-day expiry).

    Optional payload key ``exp_override`` sets absolute exp (used for MFA pending).
    """
    now = int(time.time())
    exp = int(payload.pop("exp_override", now + 86400 * 7))
    body_data = {**payload, "iat": now, "exp": exp}
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps(body_data).encode())
    signature = _sign(f"{header}.{body}")
    return f"{header}.{body}.{signature}"


def verify_token(token: str) -> dict[str, Any] | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, body, sig = parts
        if not hmac.compare_digest(_sign(f"{header}.{body}"), sig):
            return None
        payload = json.loads(_b64_decode(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def normalize_username(raw: str) -> str:
    return (raw or "").strip().lower()


def validate_username(username: str) -> str:
    uname = normalize_username(username)
    if not uname or len(uname) < 2:
        raise HTTPException(status_code=400, detail="Username must be at least 2 characters")
    if len(uname) > 64:
        raise HTTPException(status_code=400, detail="Username must be at most 64 characters")
    if not _USERNAME_RE.match(uname):
        raise HTTPException(
            status_code=400,
            detail="Username must be letters/numbers (start alnum); may include . _ -",
        )
    return uname


# ── Request / response models ──


class LoginRequest(BaseModel):
    """Simple login: both fields required."""

    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


class RegisterRequest(BaseModel):
    """Self-service signup — creates a unique platform user."""

    username: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=4, max_length=200)
    email: str | None = None

    @field_validator("username")
    @classmethod
    def _uname(cls, v: str) -> str:
        return normalize_username(v)


class LoginResponse(BaseModel):
    token: str = ""
    user: dict[str, Any] = Field(default_factory=dict)
    # MFA step-up (when user has MFA enabled): token empty, mfa_required true
    mfa_required: bool = False
    mfa_token: str | None = None


class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str = Field(..., min_length=6, max_length=8)


class MfaConfirmRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=8)


class MfaDisableRequest(BaseModel):
    password: str
    code: str = Field(..., min_length=6, max_length=8)


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    avatarUrl: str | None = None
    role: str = UserRole.USER.value
    mfaEnabled: bool = False
    authProvider: str = "local"


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=4, max_length=200)
    email: str | None = None
    role: str = UserRole.USER.value


# ── DB session injection ──

_get_db = None  # type: ignore[assignment]


def bind_get_db(get_db_fn: Any) -> None:
    global _get_db
    _get_db = get_db_fn


def get_db_dep() -> Any:
    if _get_db is None:
        raise HTTPException(status_code=500, detail="Auth DB not initialized")
    yield from _get_db()


def ensure_seed_users(db: Session) -> None:
    """Ensure bootstrap admin exists (from env). Optional SEED_USERS if set.

    Does NOT create hard-coded demo users unless SEED_USERS is configured.
    All other accounts come from /register or admin create-user.
    """
    admin = db.query(PlatformUser).filter(PlatformUser.username == _ADMIN_USERNAME).first()
    if not admin:
        admin = PlatformUser(
            id=uuid.uuid4(),
            username=_ADMIN_USERNAME,
            email=f"{_ADMIN_USERNAME}@localhost",
            password_hash=hash_password(_ADMIN_PASSWORD),
            role=UserRole.ADMIN.value,
            is_active=True,
        )
        db.add(admin)
        logger.info("Seeded platform admin user '%s'", _ADMIN_USERNAME)
    else:
        # Keep env admin password in sync when operators change ADMIN_PASSWORD
        if not verify_password(_ADMIN_PASSWORD, admin.password_hash):
            admin.password_hash = hash_password(_ADMIN_PASSWORD)
            admin.role = UserRole.ADMIN.value

    raw = (settings.seed_users or "").strip()
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            bits = part.split(":")
            uname = normalize_username(bits[0])
            pwd = bits[1].strip() if len(bits) > 1 else ""
            email = bits[2].strip() if len(bits) > 2 else f"{uname}@localhost"
            if not uname or not pwd or uname == _ADMIN_USERNAME:
                continue
            if not _USERNAME_RE.match(uname):
                continue
            existing = db.query(PlatformUser).filter(PlatformUser.username == uname).first()
            if not existing:
                db.add(
                    PlatformUser(
                        id=uuid.uuid4(),
                        username=uname,
                        email=email,
                        password_hash=hash_password(pwd),
                        role=UserRole.USER.value,
                        is_active=True,
                    )
                )
                logger.info("Seeded platform user '%s'", uname)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.warning("Seed users race/duplicate ignored")


def _user_dict(user: PlatformUser) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "username": user.username,
        "avatarUrl": None,
        "role": user.role,
        "mfaEnabled": bool(getattr(user, "mfa_enabled", False)),
        "authProvider": getattr(user, "auth_provider", None) or "local",
    }


def _payload_from_user(user: PlatformUser) -> dict[str, Any]:
    return {
        "sub": str(user.id),
        "email": user.email,
        "username": user.username,
        "role": user.role,
    }


def create_mfa_pending_token(user_id: str) -> str:
    """Short-lived token proving password step passed; MFA code still required."""
    return create_token(
        {
            "sub": str(user_id),
            "purpose": "mfa_pending",
            "exp_override": int(time.time()) + 300,
        }
    )


def verify_mfa_pending_token(token: str) -> dict[str, Any] | None:
    payload = verify_token(token)
    if not payload or payload.get("purpose") != "mfa_pending":
        return None
    return payload


def get_current_user(request: Request, db: Session = Depends(get_db_dep)) -> PlatformUser:
    """Require a valid Bearer token and return the PlatformUser."""
    if settings.auth_disabled:
        admin = db.query(PlatformUser).filter(PlatformUser.username == _ADMIN_USERNAME).first()
        if admin:
            return admin
        raise HTTPException(status_code=500, detail="Auth disabled but no admin user")

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = verify_token(auth[7:])
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("sub")
    user: PlatformUser | None = None
    if user_id:
        try:
            user = db.query(PlatformUser).filter(PlatformUser.id == uuid.UUID(str(user_id))).first()
        except (ValueError, TypeError):
            user = None
    if not user:
        uname = normalize_username(payload.get("username") or "")
        if uname:
            user = db.query(PlatformUser).filter(PlatformUser.username == uname).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db_dep)) -> PlatformUser | None:
    if settings.auth_disabled:
        return db.query(PlatformUser).filter(PlatformUser.username == _ADMIN_USERNAME).first()
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        return get_current_user(request, db)
    except HTTPException:
        return None


def require_admin(user: PlatformUser = Depends(get_current_user)) -> PlatformUser:
    if user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def user_can_access_job(user: PlatformUser, job: Any) -> bool:
    """Admin sees all; regular users only jobs they own (owner_id == user.id)."""
    if user.role == UserRole.ADMIN.value:
        return True
    owner_id = getattr(job, "owner_id", None)
    if owner_id is None:
        return False
    return str(owner_id) == str(user.id)


def _create_unique_user(
    db: Session,
    *,
    username: str,
    password: str,
    email: str | None,
    role: str,
) -> PlatformUser:
    """Insert a user with unique username; raise 400 on duplicate."""
    uname = validate_username(username)
    if db.query(PlatformUser).filter(PlatformUser.username == uname).first():
        raise HTTPException(status_code=400, detail="Username already taken")

    email_val = (email or f"{uname}@localhost").strip().lower()
    # Email must also be unique — if clash, use uname-scoped address
    if db.query(PlatformUser).filter(PlatformUser.email == email_val).first():
        email_val = f"{uname}.{uuid.uuid4().hex[:8]}@localhost"

    role_val = role if role in (UserRole.ADMIN.value, UserRole.USER.value) else UserRole.USER.value
    user = PlatformUser(
        id=uuid.uuid4(),
        username=uname,
        email=email_val,
        password_hash=hash_password(password),
        role=role_val,
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Username already taken") from None
    logger.info("Registered platform user '%s' id=%s", uname, user.id)
    return user


# ── Endpoints ──


@router.post("/register", response_model=LoginResponse, status_code=201)
async def register(
    req: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db_dep),
):
    """Create a new unique user and return a JWT (auto-login).

    Username is stored in platform_users and must be unique.
    New users get role=user and fully isolated crawls/history/reports.
    """
    from src.core.audit import record_audit

    ensure_seed_users(db)
    if len(req.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    uname = validate_username(req.username)
    if uname == _ADMIN_USERNAME:
        raise HTTPException(status_code=400, detail="Username reserved")

    try:
        user = _create_unique_user(
            db,
            username=uname,
            password=req.password,
            email=req.email,
            role=UserRole.USER.value,
        )
    except HTTPException as e:
        record_audit(
            db,
            action="auth.register",
            actor_username=uname,
            success=False,
            detail={"reason": str(e.detail)},
            request=request,
        )
        raise

    token = create_token(_payload_from_user(user))
    record_audit(
        db,
        action="auth.register",
        actor_id=user.id,
        actor_username=user.username,
        resource_type="user",
        resource_id=str(user.id),
        success=True,
        request=request,
    )
    return LoginResponse(token=token, user=_user_dict(user))


@router.post("/login", response_model=LoginResponse)
async def login(
    req: LoginRequest,
    request: Request,
    db: Session = Depends(get_db_dep),
):
    """Simple login: username + password required.

    Returns a user-scoped JWT (sub=user_id). Isolation is enforced by owner_id
    on every job — User A never sees User B history or reports.
    """
    from src.core.audit import record_audit

    ensure_seed_users(db)

    username = normalize_username(req.username)
    password = req.password
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    user = db.query(PlatformUser).filter(PlatformUser.username == username).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        # Allow env admin password if hash drifted
        if username == _ADMIN_USERNAME and password == _ADMIN_PASSWORD:
            if not user:
                ensure_seed_users(db)
                user = db.query(PlatformUser).filter(PlatformUser.username == _ADMIN_USERNAME).first()
            if user:
                user.password_hash = hash_password(_ADMIN_PASSWORD)
                db.commit()
            else:
                record_audit(
                    db,
                    action="auth.login",
                    actor_username=username,
                    success=False,
                    detail={"reason": "invalid_credentials"},
                    request=request,
                )
                raise HTTPException(status_code=401, detail="Invalid username or password")
        else:
            record_audit(
                db,
                action="auth.login",
                actor_username=username,
                success=False,
                detail={"reason": "invalid_credentials"},
                request=request,
            )
            raise HTTPException(status_code=401, detail="Invalid username or password")

    if user.role not in (UserRole.ADMIN.value, UserRole.USER.value):
        user.role = UserRole.USER.value
        db.commit()

    # MFA step-up when user enrolled TOTP (and the MFA feature is enabled)
    if settings.mfa_enabled and getattr(user, "mfa_enabled", False) and getattr(user, "mfa_secret", None):
        mfa_tok = create_mfa_pending_token(str(user.id))
        record_audit(
            db,
            action="auth.login",
            actor_id=user.id,
            actor_username=user.username,
            resource_type="user",
            resource_id=str(user.id),
            success=True,
            detail={"role": user.role, "method": "password", "mfa_required": True},
            request=request,
        )
        return LoginResponse(
            token="",
            user={},
            mfa_required=True,
            mfa_token=mfa_tok,
        )

    token = create_token(_payload_from_user(user))
    record_audit(
        db,
        action="auth.login",
        actor_id=user.id,
        actor_username=user.username,
        resource_type="user",
        resource_id=str(user.id),
        success=True,
        detail={"role": user.role, "method": "password"},
        request=request,
    )
    return LoginResponse(token=token, user=_user_dict(user))


@router.get("/me", response_model=UserResponse)
async def get_me(user: PlatformUser = Depends(get_current_user)):
    return UserResponse(
        id=str(user.id),
        email=user.email,
        username=user.username,
        avatarUrl=None,
        role=user.role,
        mfaEnabled=bool(getattr(user, "mfa_enabled", False)),
        authProvider=getattr(user, "auth_provider", None) or "local",
    )


@router.patch("/me", response_model=UserResponse)
async def update_me(
    request: Request,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
):
    try:
        body = await request.json()
    except Exception:
        body = {}

    new_username = body.get("username")
    if new_username is not None:
        uname = validate_username(str(new_username))
        if uname != user.username:
            clash = db.query(PlatformUser).filter(PlatformUser.username == uname).first()
            if clash and clash.id != user.id:
                raise HTTPException(status_code=400, detail="Username already taken")
            user.username = uname
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise HTTPException(status_code=400, detail="Username already taken") from None

    return UserResponse(
        id=str(user.id),
        email=user.email,
        username=user.username,
        avatarUrl=None,
        role=user.role,
        mfaEnabled=bool(getattr(user, "mfa_enabled", False)),
        authProvider=getattr(user, "auth_provider", None) or "local",
    )


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    req: CreateUserRequest,
    request: Request,
    admin: PlatformUser = Depends(require_admin),
    db: Session = Depends(get_db_dep),
):
    """Admin-only: create a new platform user with unique username."""
    from src.core.audit import record_audit

    user = _create_unique_user(
        db,
        username=req.username,
        password=req.password,
        email=req.email,
        role=req.role,
    )
    record_audit(
        db,
        action="admin.user_create",
        actor_id=admin.id,
        actor_username=admin.username,
        resource_type="user",
        resource_id=str(user.id),
        success=True,
        detail={"created_username": user.username, "role": user.role},
        request=request,
    )
    return UserResponse(
        id=str(user.id),
        email=user.email,
        username=user.username,
        avatarUrl=None,
        role=user.role,
    )


@router.get("/users")
async def list_users(
    admin: PlatformUser = Depends(require_admin),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Admin-only: list all registered usernames in the database."""
    users = db.query(PlatformUser).order_by(PlatformUser.created_at.asc()).all()
    return {
        "users": [_user_dict(u) for u in users],
        "total": len(users),
    }


@router.get("/audit")
async def list_audit_events(
    limit: int = 50,
    offset: int = 0,
    action: str | None = None,
    actor_username: str | None = None,
    admin: PlatformUser = Depends(require_admin),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Admin-only: list recent audit events (Phase 1)."""
    from src.models.database import AuditEvent

    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    q = db.query(AuditEvent)
    if action:
        q = q.filter(AuditEvent.action == action)
    if actor_username:
        q = q.filter(AuditEvent.actor_username == normalize_username(actor_username))
    total = q.count()
    rows = q.order_by(AuditEvent.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "events": [
            {
                "id": str(e.id),
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "actor_id": str(e.actor_id) if e.actor_id else None,
                "actor_username": e.actor_username,
                "action": e.action,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "ip_address": e.ip_address,
                "success": e.success,
                "detail": e.detail or {},
            }
            for e in rows
        ],
    }


# ── Phase 2: TOTP MFA ──────────────────────────────────────────────────────


def _require_mfa_feature() -> None:
    if not settings.mfa_enabled:
        raise HTTPException(status_code=403, detail="MFA feature disabled by administrator")


@router.post("/mfa/setup")
async def mfa_setup(
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Start MFA enrollment: returns secret + otpauth URI + QR URL. Not enabled until /mfa/confirm."""
    _require_mfa_feature()
    from src.core.mfa import generate_mfa_secret, provisioning_uri, qr_data_url_hint

    if getattr(user, "mfa_enabled", False):
        raise HTTPException(status_code=400, detail="MFA already enabled")
    secret = generate_mfa_secret()
    user.mfa_secret = secret
    user.mfa_enabled = False
    db.commit()
    uri = provisioning_uri(secret, user.username, issuer="CBTP")
    return {
        "secret": secret,
        "otpauth_uri": uri,
        "qr_url": qr_data_url_hint(uri),
        "message": "Scan with Google Authenticator / Authy, then POST /api/auth/mfa/confirm with a code",
    }


@router.post("/mfa/confirm")
async def mfa_confirm(
    req: MfaConfirmRequest,
    request: Request,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Confirm MFA enrollment with a valid TOTP code."""
    _require_mfa_feature()
    from src.core.audit import record_audit
    from src.core.mfa import verify_totp

    secret = getattr(user, "mfa_secret", None)
    if not secret:
        raise HTTPException(status_code=400, detail="Call /api/auth/mfa/setup first")
    if not verify_totp(secret, req.code):
        record_audit(
            db,
            action="auth.mfa_confirm",
            actor_id=user.id,
            actor_username=user.username,
            success=False,
            request=request,
        )
        raise HTTPException(status_code=400, detail="Invalid MFA code")
    user.mfa_enabled = True
    db.commit()
    record_audit(
        db,
        action="auth.mfa_enable",
        actor_id=user.id,
        actor_username=user.username,
        success=True,
        request=request,
    )
    return {"mfaEnabled": True, "message": "MFA enabled"}


@router.post("/mfa/disable")
async def mfa_disable(
    req: MfaDisableRequest,
    request: Request,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Disable MFA (requires password + current TOTP)."""
    _require_mfa_feature()
    from src.core.audit import record_audit
    from src.core.mfa import verify_totp

    if not getattr(user, "mfa_enabled", False):
        return {"mfaEnabled": False}
    if not user.password_hash or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid password")
    if not verify_totp(user.mfa_secret or "", req.code):
        raise HTTPException(status_code=400, detail="Invalid MFA code")
    user.mfa_enabled = False
    user.mfa_secret = None
    db.commit()
    record_audit(
        db,
        action="auth.mfa_disable",
        actor_id=user.id,
        actor_username=user.username,
        success=True,
        request=request,
    )
    return {"mfaEnabled": False}


@router.post("/mfa/verify", response_model=LoginResponse)
async def mfa_verify(
    req: MfaVerifyRequest,
    request: Request,
    db: Session = Depends(get_db_dep),
) -> LoginResponse:
    """Complete login after password step when MFA is required."""
    _require_mfa_feature()
    from src.core.audit import record_audit
    from src.core.mfa import verify_totp

    pending = verify_mfa_pending_token(req.mfa_token)
    if not pending:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA session")
    try:
        uid = uuid.UUID(str(pending["sub"]))
    except (ValueError, KeyError) as err:
        raise HTTPException(status_code=401, detail="Invalid MFA session") from err
    user = db.query(PlatformUser).filter(PlatformUser.id == uid).first()
    if not user or not user.is_active or not user.mfa_enabled or not user.mfa_secret:
        raise HTTPException(status_code=401, detail="MFA not available for user")
    if not verify_totp(user.mfa_secret, req.code):
        record_audit(
            db,
            action="auth.mfa_verify",
            actor_id=user.id,
            actor_username=user.username,
            success=False,
            request=request,
        )
        raise HTTPException(status_code=401, detail="Invalid MFA code")
    token = create_token(_payload_from_user(user))
    record_audit(
        db,
        action="auth.mfa_verify",
        actor_id=user.id,
        actor_username=user.username,
        success=True,
        detail={"method": "totp"},
        request=request,
    )
    return LoginResponse(token=token, user=_user_dict(user), mfa_required=False)


@router.get("/mfa/status")
async def mfa_status(user: PlatformUser = Depends(get_current_user)) -> dict[str, Any]:
    _require_mfa_feature()
    from src.core.mfa import mfa_status_dict

    return mfa_status_dict(user)


# ── Phase 3: OIDC (real IdP — configure via env) ────────────────────────────


@router.get("/oidc/status")
async def oidc_status() -> dict[str, Any]:
    from src.core.oidc import oidc_configured

    return {
        "oidcEnabled": bool(settings.oidc_enabled),
        "oidcConfigured": oidc_configured(),
        "issuer": settings.oidc_issuer if settings.oidc_enabled else None,
        "loginUrl": "/api/auth/oidc/login" if oidc_configured() else None,
    }


@router.get("/oidc/login")
async def oidc_login(db: Session = Depends(get_db_dep)):
    """Redirect browser to the configured IdP (Authorization Code + PKCE)."""
    from datetime import UTC, datetime, timedelta

    from fastapi.responses import RedirectResponse

    from src.core.oidc import (
        build_authorize_url,
        generate_pkce,
        generate_state_nonce,
        oidc_configured,
    )
    from src.models.database import OidcState

    if not oidc_configured():
        raise HTTPException(
            status_code=503,
            detail="OIDC not configured. Set OIDC_ENABLED=true and OIDC_ISSUER/CLIENT_ID/REDIRECT_URI.",
        )
    state, nonce = generate_state_nonce()
    verifier, challenge = generate_pkce()
    row = OidcState(
        state=state,
        nonce=nonce,
        code_verifier=verifier,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db.add(row)
    db.commit()
    url = await build_authorize_url(state=state, nonce=nonce, code_challenge=challenge)
    return RedirectResponse(url=url, status_code=302)


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db_dep),
):
    """IdP redirect target: exchange code, upsert user, return JWT JSON (or redirect with token)."""
    from datetime import UTC, datetime

    from src.core.audit import record_audit
    from src.core.oidc import (
        exchange_code,
        fetch_userinfo,
        oidc_configured,
        verify_id_token,
    )
    from src.models.database import OidcState

    if error:
        raise HTTPException(status_code=400, detail=f"OIDC error: {error}")
    if not oidc_configured():
        raise HTTPException(status_code=503, detail="OIDC not configured")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    row = db.query(OidcState).filter(OidcState.state == state).first()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid OIDC state")
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if exp < datetime.now(UTC):
        db.delete(row)
        db.commit()
        raise HTTPException(status_code=400, detail="OIDC state expired")
    verifier = row.code_verifier
    expected_nonce = row.nonce
    db.delete(row)
    db.commit()

    try:
        tokens = await exchange_code(code, verifier)
    except Exception as e:
        record_audit(
            db,
            action="auth.oidc_login",
            success=False,
            detail={"reason": str(e)[:200]},
            request=request,
        )
        raise HTTPException(status_code=400, detail="OIDC token exchange failed") from None

    id_token = tokens.get("id_token") or ""
    access = tokens.get("access_token") or ""
    claims: dict[str, Any] = {}
    if id_token:
        try:
            claims = await verify_id_token(id_token, nonce=expected_nonce)
        except Exception as e:
            record_audit(
                db,
                action="auth.oidc_login",
                success=False,
                detail={"reason": f"id_token rejected: {str(e)[:200]}"},
                request=request,
            )
            raise HTTPException(status_code=400, detail="OIDC id_token verification failed") from e
    if access and (not claims.get("email") or not claims.get("sub")):
        try:
            ui = await fetch_userinfo(access)
            claims = {**ui, **claims}
        except Exception:
            pass

    sub = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip().lower()
    name = str(claims.get("preferred_username") or claims.get("name") or email or sub).strip()
    if not sub:
        raise HTTPException(status_code=400, detail="OIDC token missing subject")

    user = db.query(PlatformUser).filter(PlatformUser.oidc_subject == sub).first()
    if not user and email:
        user = db.query(PlatformUser).filter(PlatformUser.email == email).first()
        if user:
            user.oidc_subject = sub
            user.auth_provider = "oidc"
            db.commit()

    if not user:
        # Create unique username from email/name
        base = normalize_username(name.split("@")[0] if "@" in name else name)
        base = re.sub(r"[^a-z0-9._-]", "", base) or f"user{sub[:8]}"
        uname = base[:50]
        n = 0
        while db.query(PlatformUser).filter(PlatformUser.username == uname).first():
            n += 1
            uname = f"{base[:40]}{n}"
        email_val = email or f"{uname}@oidc.local"
        if db.query(PlatformUser).filter(PlatformUser.email == email_val).first():
            email_val = f"{uname}.{uuid.uuid4().hex[:6]}@oidc.local"
        user = PlatformUser(
            id=uuid.uuid4(),
            username=uname,
            email=email_val,
            password_hash="",  # OIDC-only
            role=UserRole.USER.value,
            is_active=True,
            auth_provider="oidc",
            oidc_subject=sub,
            mfa_enabled=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    if not user.is_active:
        raise HTTPException(status_code=401, detail="User inactive")

    token = create_token(_payload_from_user(user))
    record_audit(
        db,
        action="auth.oidc_login",
        actor_id=user.id,
        actor_username=user.username,
        success=True,
        detail={"provider": "oidc", "sub": sub[:32]},
        request=request,
    )
    # Browser flow: redirect to the UI with the JWT in the URL fragment
    # (fragments are not sent to servers or written to access logs).
    ui_url = (getattr(settings, "oidc_ui_success_url", "") or "").strip()
    if ui_url:
        from urllib.parse import quote as _quote

        from fastapi.responses import RedirectResponse

        return RedirectResponse(url=f"{ui_url}#token={_quote(token)}", status_code=302)
    # API-client flow: JSON token response
    return {
        "token": token,
        "user": _user_dict(user),
        "mfa_required": False,
    }
