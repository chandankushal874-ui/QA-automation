"""SQLAlchemy data models for the cross-browser testing platform.

Models:
- Job: A testing job submitted by the user
- CrawlState: URL/state-hash with visit count and depth
- Issue: A deduplicated bug/issue found during crawling
- IssueOccurrence: A single occurrence of an issue on a specific page/browser
- Journey: A multi-step user flow (login → browse → action → confirm)
- AuthProfile: Stored authentication credentials/state for a job
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    """Base class for all models."""

    type_annotation_map = {
        dict[str, Any]: JSON_TYPE,
    }


def utc_now() -> datetime:
    return datetime.now(UTC)


# ─── Enums ───


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IssueSeverity(StrEnum):
    BLOCKING = "blocking"
    FUNCTIONAL = "functional"
    VISUAL = "visual"
    ADVISORY = "advisory"


class IssueCategory(StrEnum):
    JS_ERROR = "js_error"
    NETWORK_FAILURE = "network_failure"
    HTTP_ERROR = "http_error"
    CONSOLE_ERROR = "console_error"
    CORS_ERROR = "cors_error"
    CSP_VIOLATION = "csp_violation"
    VISUAL_REGRESSION = "visual_regression"
    ACCESSIBILITY = "accessibility"
    PERFORMANCE = "performance"
    SECURITY = "security"
    MIXED_CONTENT = "mixed_content"
    RESOURCE_LOAD_FAILURE = "resource_load_failure"
    FRAMEWORK_ERROR = "framework_error"


class BrowserEngine(StrEnum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class CrawlStateStatus(StrEnum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    VISITING = "visiting"
    VISITED = "visited"
    FAILED = "failed"
    SKIPPED = "skipped"


class JourneyStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ─── Models ───


class UserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class PlatformUser(Base):
    """Login account for the platform (admin or regular user).

    Separate from AuthProfile, which stores credentials for the *target site*
    being crawled. PlatformUser is who is using CBTP itself.
    """

    __tablename__ = "platform_users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # Empty string allowed for OIDC-only accounts (no local password).
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(20), default=UserRole.USER.value, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    # Phase 2 — TOTP MFA (opt-in). Secret is base32; only set when enrolled.
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Phase 3 — OIDC. auth_provider: "local" | "oidc"
    auth_provider: Mapped[str] = mapped_column(String(20), default="local", nullable=False)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)

    jobs: Mapped[list[Job]] = relationship(back_populates="owner")

    __table_args__ = (
        Index("ix_platform_users_username", "username"),
        Index("ix_platform_users_email", "email"),
        Index("ix_platform_users_oidc_subject", "oidc_subject"),
    )


class OidcState(Base):
    """Short-lived OIDC login state (CSRF + PKCE)."""

    __tablename__ = "oidc_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEvent(Base):
    """Append-only security/ops audit trail (Phase 1 enterprise controls).

    Never update/delete rows in application code — insert only.
    Used for login, register, job lifecycle, admin actions.
    MFA/OIDC events will use the same table (Phase 2–3).
    """

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    __table_args__ = (
        Index("ix_audit_events_created_at", "created_at"),
        Index("ix_audit_events_actor_id", "actor_id"),
        Index("ix_audit_events_action", "action"),
    )


class Job(Base):
    """A testing job submitted by the user."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Owner of this job (platform user). NULL = legacy job from before multi-user.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    scope: Mapped[str] = mapped_column(
        String(50), default="same-domain", nullable=False
    )  # same-domain, same-origin, custom
    status: Mapped[str] = mapped_column(
        Enum(JobStatus, name="job_status", create_constraint=True),
        default=JobStatus.PENDING,
        nullable=False,
    )

    # Browser matrix configuration
    browser_matrix: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=lambda: {"chromium": True, "firefox": True, "webkit": True},
        nullable=False,
    )

    # Crawl budget configuration
    max_pages: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    max_depth: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_time_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)

    # Results summary
    pages_discovered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_visited: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    issues_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    console_errors_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    endpoints_discovered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Auth configuration
    has_credentials: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auth_method_used: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Source repo (optional, for fix-suggestion engine)
    repo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Configuration (flexible JSON)
    config: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    # Relationships
    owner: Mapped[PlatformUser | None] = relationship(back_populates="jobs")
    crawl_states: Mapped[list[CrawlState]] = relationship(back_populates="job", cascade="all, delete-orphan")
    issues: Mapped[list[Issue]] = relationship(back_populates="job", cascade="all, delete-orphan")
    journeys: Mapped[list[Journey]] = relationship(back_populates="job", cascade="all, delete-orphan")
    auth_profile: Mapped[AuthProfile | None] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_created_at", "created_at"),
        Index("ix_jobs_owner_id", "owner_id"),
    )


class CrawlState(Base):
    """A URL/state that has been discovered or visited during crawling."""

    __tablename__ = "crawl_states"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    url_template: Mapped[str | None] = mapped_column(String(2048), nullable=True)  # /users/{id} collapsed form
    method: Mapped[str] = mapped_column(String(10), default="GET", nullable=False)
    state_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # DOM structural hash
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    visit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(CrawlStateStatus, name="crawl_state_status", create_constraint=True),
        default=CrawlStateStatus.DISCOVERED,
        nullable=False,
    )
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    requires_auth: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Discovery metadata
    discovered_via: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # dom_link, js_analysis, brute_force, etc.
    discovered_on_page: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    endpoint_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # api, page, graphql, admin, debug, etc.

    # Console errors captured on this page
    console_errors: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    # Timing
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_visited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationship
    job: Mapped[Job] = relationship(back_populates="crawl_states")

    __table_args__ = (
        Index("ix_crawl_states_job_url", "job_id", "url"),
        Index("ix_crawl_states_job_status", "job_id", "status"),
        Index("ix_crawl_states_priority", "job_id", "priority_score"),
    )


class Issue(Base):
    """A deduplicated bug/issue found during crawling."""

    __tablename__ = "issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)  # Dedup fingerprint
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    severity: Mapped[str] = mapped_column(
        Enum(IssueSeverity, name="issue_severity", create_constraint=True),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(
        Enum(IssueCategory, name="issue_category", create_constraint=True),
        nullable=False,
    )

    # Cross-browser attribution
    browsers_affected: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=lambda: {"chromium": False, "firefox": False, "webkit": False},
        nullable=False,
    )
    browser_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 1, 2, or 3

    # Occurrence tracking
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_seen_url: Mapped[str] = mapped_column(String(2048), nullable=False)

    # Error details
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stack_trace: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_line: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Reproduction
    repro_steps: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    trace_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    har_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Fix status (for Phase V-X)
    fix_status: Mapped[str] = mapped_column(
        String(50), default="unfixed", nullable=False
    )  # unfixed, suggested, verified, pr_opened, resolved
    fix_diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    fix_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_symptom_mask: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    job: Mapped[Job] = relationship(back_populates="issues")
    occurrences: Mapped[list[IssueOccurrence]] = relationship(back_populates="issue", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_issues_job_fingerprint", "job_id", "fingerprint", unique=True),
        Index("ix_issues_job_severity", "job_id", "severity"),
        Index("ix_issues_category", "category"),
    )


class IssueOccurrence(Base):
    """A single occurrence of an issue on a specific page/browser."""

    __tablename__ = "issue_occurrences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    browser: Mapped[str] = mapped_column(
        Enum(BrowserEngine, name="browser_engine", create_constraint=True),
        nullable=False,
    )
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    console_output: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    screenshot_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Relationship
    issue: Mapped[Issue] = relationship(back_populates="occurrences")

    __table_args__ = (
        Index("ix_occurrences_issue_id", "issue_id"),
        Index("ix_occurrences_browser", "browser"),
    )


class Journey(Base):
    """A multi-step user flow treated as a first-class object."""

    __tablename__ = "journeys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(JourneyStatus, name="journey_status", create_constraint=True),
        default=JourneyStatus.PLANNED,
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # Higher = more important
    is_critical_path: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Steps definition
    steps: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE, default=list, nullable=False
    )  # [{action, selector, url, ...}, ...]

    # Results per browser
    results: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE, default=dict, nullable=False
    )  # {chromium: {passed, errors, ...}, ...}

    # Console errors captured during journey
    console_errors: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    # Timing
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Relationship
    job: Mapped[Job] = relationship(back_populates="journeys")

    __table_args__ = (
        Index("ix_journeys_job_status", "job_id", "status"),
        Index("ix_journeys_priority", "job_id", "priority"),
    )


class AuthProfile(Base):
    """Stored authentication credentials/state for a job."""

    __tablename__ = "auth_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Credentials (encrypted in production — plaintext here for dev)
    login_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(String(500), nullable=True)  # Should be encrypted

    # Auth method used and state
    auth_method: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # form_login, token_injection, cookie_replay, etc.
    is_authenticated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Captured session state
    storage_state: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE, nullable=True)
    cookies: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    tokens: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    headers: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    # Auth bypass attempt log
    attempt_log: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=list, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationship
    job: Mapped[Job] = relationship(back_populates="auth_profile")
