"""Tests for the authentication bypass engine."""

from __future__ import annotations

import pytest

from src.auth.bypass import (
    AUTH_HEADER_FORMATS,
    DEFAULT_CREDENTIALS,
    AuthAttempt,
    AuthBypassEngine,
    AuthContext,
    AuthMethod,
    AuthResult,
    AuthState,
    CookieReplayStrategy,
    DefaultCredentialStrategy,
    FormLoginStrategy,
    JWTManipulationStrategy,
    TokenInjectionStrategy,
)

# ─── AuthState tests ───


class TestAuthState:
    """Test the AuthState data class."""

    def test_initial_state_not_authenticated(self) -> None:
        state = AuthState()
        assert state.is_authenticated is False
        assert state.method_used is None
        assert state.summary == "Not authenticated (0 attempt(s) tried)"

    def test_record_successful_attempt(self) -> None:
        state = AuthState()
        attempt = AuthAttempt(
            method=AuthMethod.FORM_LOGIN,
            result=AuthResult.SUCCESS,
            target_url="https://example.com/login",
            cookies_captured=[{"name": "session", "value": "abc123"}],
        )
        state.record_attempt(attempt)

        assert state.is_authenticated is True
        assert state.method_used == AuthMethod.FORM_LOGIN
        assert len(state.cookies) == 1
        assert state.authenticated_at is not None

    def test_record_failed_attempt_stays_unauthenticated(self) -> None:
        state = AuthState()
        attempt = AuthAttempt(
            method=AuthMethod.FORM_LOGIN,
            result=AuthResult.FAILURE,
            target_url="https://example.com/login",
        )
        state.record_attempt(attempt)

        assert state.is_authenticated is False
        assert len(state.attempts) == 1
        assert state.summary == "Not authenticated (1 attempt(s) tried)"

    def test_record_partial_success(self) -> None:
        state = AuthState()
        attempt = AuthAttempt(
            method=AuthMethod.TOKEN_INJECTION,
            result=AuthResult.PARTIAL,
            target_url="https://example.com",
            tokens_captured={"access_token": "tok123"},
        )
        state.record_attempt(attempt)

        assert state.is_authenticated is True
        assert state.method_used == AuthMethod.TOKEN_INJECTION
        assert state.tokens["access_token"] == "tok123"

    def test_get_auth_headers_with_bearer_token(self) -> None:
        state = AuthState()
        state.tokens = {"access_token": "my_token"}
        state.is_authenticated = True

        headers = state.get_auth_headers()
        assert headers["Authorization"] == "Bearer my_token"

    def test_get_auth_headers_with_generic_token(self) -> None:
        state = AuthState()
        state.tokens = {"token": "generic_tok"}
        state.is_authenticated = True

        headers = state.get_auth_headers()
        assert headers["Authorization"] == "Bearer generic_tok"

    def test_get_auth_headers_with_custom_headers(self) -> None:
        state = AuthState()
        state.headers = {"X-API-Key": "key123"}
        state.is_authenticated = True

        headers = state.get_auth_headers()
        assert headers["X-API-Key"] == "key123"


# ─── AuthAttempt tests ───


class TestAuthAttempt:
    """Test the AuthAttempt data class."""

    def test_success_is_succeeded(self) -> None:
        attempt = AuthAttempt(method=AuthMethod.FORM_LOGIN, result=AuthResult.SUCCESS)
        assert attempt.succeeded is True

    def test_partial_is_succeeded(self) -> None:
        attempt = AuthAttempt(method=AuthMethod.FORM_LOGIN, result=AuthResult.PARTIAL)
        assert attempt.succeeded is True

    def test_failure_is_not_succeeded(self) -> None:
        attempt = AuthAttempt(method=AuthMethod.FORM_LOGIN, result=AuthResult.FAILURE)
        assert attempt.succeeded is False

    def test_skipped_is_not_succeeded(self) -> None:
        attempt = AuthAttempt(method=AuthMethod.FORM_LOGIN, result=AuthResult.SKIPPED)
        assert attempt.succeeded is False

    def test_error_is_not_succeeded(self) -> None:
        attempt = AuthAttempt(method=AuthMethod.FORM_LOGIN, result=AuthResult.ERROR)
        assert attempt.succeeded is False


# ─── AuthContext tests ───


class TestAuthContext:
    """Test the AuthContext data class."""

    def test_has_credentials_with_username(self) -> None:
        ctx = AuthContext(
            target_url="https://example.com",
            credentials={"username": "admin", "password": "pass"},
        )
        assert ctx.has_credentials is True

    def test_has_credentials_empty(self) -> None:
        ctx = AuthContext(target_url="https://example.com")
        assert ctx.has_credentials is False

    def test_has_credentials_empty_strings(self) -> None:
        ctx = AuthContext(
            target_url="https://example.com",
            credentials={"username": "", "password": ""},
        )
        assert ctx.has_credentials is False


# ─── Strategy tests ───


class TestFormLoginStrategy:
    """Test the form login strategy."""

    @pytest.mark.asyncio
    async def test_skips_without_credentials(self) -> None:
        strategy = FormLoginStrategy()
        assert strategy.method == AuthMethod.FORM_LOGIN
        assert strategy.requires_localhost is False

        ctx = AuthContext(target_url="https://example.com")
        result = await strategy.attempt(ctx)
        assert result.result == AuthResult.SKIPPED
        assert "No credentials" in result.notes


class TestCookieReplayStrategy:
    """Test the cookie replay strategy."""

    @pytest.mark.asyncio
    async def test_skips_without_cookies(self) -> None:
        strategy = CookieReplayStrategy()
        assert strategy.method == AuthMethod.COOKIE_REPLAY

        ctx = AuthContext(target_url="https://example.com")
        result = await strategy.attempt(ctx)
        assert result.result == AuthResult.SKIPPED
        assert "No existing cookies" in result.notes


class TestTokenInjectionStrategy:
    """Test the token injection strategy."""

    @pytest.mark.asyncio
    async def test_skips_without_tokens(self) -> None:
        strategy = TokenInjectionStrategy()
        assert strategy.method == AuthMethod.TOKEN_INJECTION

        ctx = AuthContext(target_url="https://example.com")
        result = await strategy.attempt(ctx)
        assert result.result == AuthResult.SKIPPED


class TestDefaultCredentialStrategy:
    """Test the default credential strategy."""

    @pytest.mark.asyncio
    async def test_skips_on_non_localhost(self) -> None:
        strategy = DefaultCredentialStrategy()
        assert strategy.requires_localhost is True

        ctx = AuthContext(target_url="https://example.com", is_localhost=False)
        result = await strategy.attempt(ctx)
        assert result.result == AuthResult.SKIPPED
        assert "non-localhost" in result.notes


class TestJWTManipulationStrategy:
    """Test the JWT manipulation strategy."""

    @pytest.mark.asyncio
    async def test_skips_without_jwts(self) -> None:
        strategy = JWTManipulationStrategy()
        assert strategy.method == AuthMethod.JWT_MANIPULATION

        ctx = AuthContext(target_url="https://example.com")
        result = await strategy.attempt(ctx)
        assert result.result == AuthResult.SKIPPED
        assert "No JWTs found" in result.notes

    @pytest.mark.asyncio
    async def test_finds_jwts_in_tokens(self) -> None:
        strategy = JWTManipulationStrategy()
        # A real-ish JWT structure (not a valid signature, but valid format)
        fake_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiYWRtaW4ifQ.dGVzdA"

        ctx = AuthContext(
            target_url="https://example.com",
            existing_tokens={"access_token": fake_jwt},
        )
        result = await strategy.attempt(ctx)
        assert "Found 1 JWT" in result.notes

    def test_decode_jwt_payload(self) -> None:
        # eyJ1c2VyIjoiYWRtaW4ifQ == {"user": "admin"}
        fake_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiYWRtaW4ifQ.dGVzdA"
        payload = JWTManipulationStrategy.decode_jwt_payload(fake_jwt)
        assert payload is not None
        assert payload["user"] == "admin"

    def test_decode_invalid_jwt(self) -> None:
        payload = JWTManipulationStrategy.decode_jwt_payload("not.a.jwt")
        assert payload is None


# ─── AuthBypassEngine tests ───


class TestAuthBypassEngine:
    """Test the auth bypass orchestrator."""

    def test_has_default_strategies(self) -> None:
        engine = AuthBypassEngine()
        assert len(engine.strategies) == 5
        methods = [s.method for s in engine.strategies]
        assert AuthMethod.FORM_LOGIN in methods
        assert AuthMethod.COOKIE_REPLAY in methods
        assert AuthMethod.TOKEN_INJECTION in methods
        assert AuthMethod.DEFAULT_CREDENTIALS in methods
        assert AuthMethod.JWT_MANIPULATION in methods

    @pytest.mark.asyncio
    async def test_authenticate_tries_all_strategies(self) -> None:
        engine = AuthBypassEngine()
        ctx = AuthContext(target_url="https://example.com", is_localhost=False)

        state = await engine.authenticate(ctx)

        # All strategies should have been tried (and skipped since no creds/cookies/tokens)
        assert state.is_authenticated is False
        assert len(state.attempts) == 5  # All 5 strategies attempted

    @pytest.mark.asyncio
    async def test_authenticate_skips_localhost_strategies_on_remote(self) -> None:
        engine = AuthBypassEngine()
        ctx = AuthContext(target_url="https://remote-site.com", is_localhost=False)

        state = await engine.authenticate(ctx)

        # DefaultCredentialStrategy should be skipped for non-localhost
        default_cred_attempts = [a for a in state.attempts if a.method == AuthMethod.DEFAULT_CREDENTIALS]
        assert len(default_cred_attempts) == 1
        assert default_cred_attempts[0].result == AuthResult.SKIPPED

    def test_get_attempt_log(self) -> None:
        engine = AuthBypassEngine()
        engine.state.record_attempt(
            AuthAttempt(
                method=AuthMethod.FORM_LOGIN,
                result=AuthResult.FAILURE,
                target_url="https://example.com",
                notes="Test attempt",
            )
        )

        log = engine.get_attempt_log()
        assert len(log) == 1
        assert log[0]["method"] == "form_login"
        assert log[0]["result"] == "failure"

    def test_add_custom_strategy(self) -> None:
        engine = AuthBypassEngine()
        initial_count = len(engine.strategies)

        # Add another strategy
        engine.add_strategy(FormLoginStrategy())  # duplicate for testing
        assert len(engine.strategies) == initial_count + 1


# ─── Constants tests ───


class TestConstants:
    """Test that important constants are properly defined."""

    def test_default_credentials_populated(self) -> None:
        assert len(DEFAULT_CREDENTIALS) >= 10
        assert ("admin", "admin") in DEFAULT_CREDENTIALS
        assert ("root", "root") in DEFAULT_CREDENTIALS

    def test_auth_header_formats_populated(self) -> None:
        assert len(AUTH_HEADER_FORMATS) >= 5
        assert "Bearer" in AUTH_HEADER_FORMATS
        assert "Basic" in AUTH_HEADER_FORMATS
        assert "API-Key" in AUTH_HEADER_FORMATS
