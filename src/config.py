"""Cross-Browser Testing Platform — Core configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings

# Resolve the .env file relative to the project root (this file's parent's parent),
# so the API server can be launched from any working directory and still load keys.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    database_url: str = "postgresql+asyncpg://cbtp:cbtp_dev_password@localhost:5432/cbtp"
    database_url_sync: str = "postgresql://cbtp:cbtp_dev_password@localhost:5432/cbtp"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # S3 / MinIO
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "cbtp_minio"
    s3_secret_key: str = "cbtp_minio_password"
    s3_bucket_name: str = "cbtp-artifacts"

    # OpenRouter (LLM)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_default_model: str = "openai/gpt-oss-20b:free"

    # GitHub
    github_token: str = ""

    # E2B (remote sandbox for fix verification - Step W)
    e2b_api_key: str = ""
    e2b_sandbox_timeout: int = 120  # seconds a sandbox stays alive before auto-kill

    # Daytona (alternative remote sandbox for fix verification)
    daytona_api_key: str = ""
    daytona_api_url: str = "https://app.daytona.io/api"
    daytona_sandbox_timeout: int = 120  # seconds before auto-stop
    # Optional: reuse a long-lived sandbox by ID instead of creating/deleting
    # one per verification. Required when the org has no default region set
    # (create() fails but an existing started sandbox can be reused).
    daytona_sandbox_id: str = ""

    # Firecrawl (optional)
    firecrawl_api_key: str = ""

    # Crawl budgets
    max_crawl_depth: int = 10
    max_pages_per_job: int = 1000
    max_crawl_time_seconds: int = 3600
    per_endpoint_visit_budget: int = 5
    circuit_breaker_threshold: int = 10

    # Fixture app
    fixture_app_url: str = "http://localhost:8090"

    # Self-hosted auth — only bootstrap admin from env (no hard-coded demo users).
    # Everyone else registers via POST /api/auth/register (unique username).
    admin_password: str = "admin123"
    admin_username: str = "admin"
    # Optional comma-separated seed users: username:password:email
    # Leave empty for simple login — users self-register.
    seed_users: str = ""
    # When false, all /jobs* routes require a valid Bearer token
    auth_disabled: bool = False
    # JWT signing secret (falls back to admin_password if empty)
    jwt_secret: str = ""
    # Max simultaneous crawls (pending+running) per non-admin user.
    # Example: 20 users × 2 slots = 40 isolated crawls system-wide (needs enough RQ workers).
    max_concurrent_jobs_per_user: int = 2
    # Admin concurrent-job cap (0 = unlimited)
    max_concurrent_jobs_admin: int = 0
    # When true, crawls open Redis on a per-tenant logical DB (1–15)
    tenant_redis_isolation: bool = True

    # ── Enterprise auth controls (phased; defaults keep local simple) ──
    # Phase 1: append-only audit log (login, jobs, admin). Safe default: on.
    audit_log_enabled: bool = True
    # Phase 2: TOTP MFA (per-user opt-in). When false, /api/auth/mfa/* is disabled.
    mfa_enabled: bool = True
    # Phase 3: Real IdP via OIDC. Off until configured.
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:8000/api/auth/oidc/callback"
    oidc_scopes: str = "openid profile email"
    # Browser redirect target after successful OIDC login (token appended as #token=).
    # Empty = return the JWT as JSON instead of redirecting.
    oidc_ui_success_url: str = "http://localhost:3000/login"
    # INSECURE dev escape hatch: accept id_tokens without JWKS signature checks.
    oidc_allow_unverified_tokens: bool = False

    # Environment profiles & credentials isolation settings
    environment: str = "dev"  # dev, qa, staging, prod
    dev_credentials: str = '{"username": "admin", "password": "dev-password"}'
    qa_credentials: str = '{"username": "qa_admin", "password": "qa-password"}'
    staging_credentials: str = '{"username": "stage_admin", "password": "stage-password"}'
    prod_credentials: str = '{"username": "prod_admin", "password": "prod-password"}'

    model_config = {"env_file": str(_ENV_FILE), "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
