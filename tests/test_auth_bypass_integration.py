"""Integration tests for the authentication bypass engine.

Spawns the local fixture app in a background thread/process,
and runs Playwright-based auth bypass strategies against it.
"""

from __future__ import annotations

import pytest

from src.auth.bypass import (
    AuthContext,
    AuthResult,
    CookieReplayStrategy,
    DefaultCredentialStrategy,
    FormLoginStrategy,
    TokenInjectionStrategy,
)
from src.browser.factory import BrowserConfig, BrowserContextFactory
from src.core.endpoint_registry import EndpointRegistry


@pytest.mark.asyncio
@pytest.mark.browser
async def test_form_login_success(fixture_app):
    """Test successful authentication using form login credential fill."""
    async with BrowserContextFactory() as factory:
        config = BrowserConfig(engine="chromium", headless=True)
        registry = EndpointRegistry()

        # Create page
        inst_page = await factory.create_page(config, endpoint_registry=registry)

        # Set up auth context
        ctx = AuthContext(
            target_url=f"{fixture_app}/dashboard",
            login_url=f"{fixture_app}/login",
            credentials={"username": "user", "password": "password"},
            page=inst_page.page,
            browser_context=inst_page.page.context,
            is_localhost=True,
        )

        strategy = FormLoginStrategy()
        attempt = await strategy.attempt(ctx)

        try:
            assert attempt.result == AuthResult.SUCCESS
            assert attempt.succeeded is True
            assert len(attempt.cookies_captured) > 0
            assert attempt.storage_state is not None
            assert "Successfully authenticated" in attempt.notes

            # Verify page URL changed to dashboard
            assert inst_page.page.url.endswith("/dashboard")
        except AssertionError as e:
            print("FAILED PAGE URL:", inst_page.page.url)
            print("FAILED PAGE CONTENT:", await inst_page.page.content())
            raise e
        finally:
            await inst_page.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_default_credentials_success(fixture_app):
    """Test default credentials scanning against the login form."""
    async with BrowserContextFactory() as factory:
        config = BrowserConfig(engine="chromium", headless=True)

        inst_page = await factory.create_page(config)

        ctx = AuthContext(
            target_url=f"{fixture_app}/dashboard",
            login_url=f"{fixture_app}/login",
            page=inst_page.page,
            browser_context=inst_page.page.context,
            is_localhost=True,
        )

        strategy = DefaultCredentialStrategy()
        attempt = await strategy.attempt(ctx)

        assert attempt.result == AuthResult.SUCCESS
        assert attempt.succeeded is True
        assert (
            attempt.credentials_used == "admin:admin"
            or attempt.credentials_used == "test:test"
            or attempt.credentials_used == "user:password"
        )
        assert len(attempt.cookies_captured) > 0

        await inst_page.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_cookie_replay(fixture_app):
    """Test that session cookies can be replayed to skip form login."""
    async with BrowserContextFactory() as factory:
        config = BrowserConfig(engine="chromium", headless=True)

        inst_page = await factory.create_page(config)

        # Step 1: Perform form login to capture cookies
        ctx_login = AuthContext(
            target_url=f"{fixture_app}/dashboard",
            login_url=f"{fixture_app}/login",
            credentials={"username": "user", "password": "password"},
            page=inst_page.page,
            browser_context=inst_page.page.context,
            is_localhost=True,
        )

        login_strategy = FormLoginStrategy()
        login_attempt = await login_strategy.attempt(ctx_login)
        assert login_attempt.result == AuthResult.SUCCESS
        cookies = login_attempt.cookies_captured

        await inst_page.close()

        # Step 2: Open a fresh new page/context and replay cookies
        inst_page2 = await factory.create_page(config)

        ctx_replay = AuthContext(
            target_url=f"{fixture_app}/dashboard",
            login_url=f"{fixture_app}/login",
            existing_cookies=cookies,
            page=inst_page2.page,
            browser_context=inst_page2.page.context,
            is_localhost=True,
        )

        replay_strategy = CookieReplayStrategy()
        replay_attempt = await replay_strategy.attempt(ctx_replay)

        assert replay_attempt.result == AuthResult.SUCCESS
        assert replay_attempt.succeeded is True
        assert inst_page2.page.url.endswith("/dashboard")

        await inst_page2.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_token_injection(fixture_app):
    """Test authorization token injection."""
    async with BrowserContextFactory() as factory:
        config = BrowserConfig(engine="chromium", headless=True)

        inst_page = await factory.create_page(config)

        # Obtain a valid token by logging in via POST API call
        # Since uvicorn is running, we could do this via login API or mock
        # Let's mock a simple authorization header / token mapping
        # Our target app accepts cookies or authorization headers.
        # Let's perform form login and capture the returned cookie value as token.
        ctx_login = AuthContext(
            target_url=f"{fixture_app}/dashboard",
            login_url=f"{fixture_app}/login",
            credentials={"username": "user", "password": "password"},
            page=inst_page.page,
            browser_context=inst_page.page.context,
            is_localhost=True,
        )

        login_strategy = FormLoginStrategy()
        login_attempt = await login_strategy.attempt(ctx_login)
        assert login_attempt.result == AuthResult.SUCCESS

        # Extract the session id value from the captured cookie
        session_id = None
        for cookie in login_attempt.cookies_captured:
            if cookie.get("name") == "session_id":
                session_id = cookie.get("value")
                break

        assert session_id is not None
        await inst_page.close()

        # Create a new page and inject this session_id as a Bearer token
        inst_page2 = await factory.create_page(config)

        ctx_inject = AuthContext(
            target_url=f"{fixture_app}/dashboard",
            login_url=f"{fixture_app}/login",
            existing_tokens={"access_token": session_id},
            page=inst_page2.page,
            browser_context=inst_page2.page.context,
            is_localhost=True,
        )

        inject_strategy = TokenInjectionStrategy()
        inject_attempt = await inject_strategy.attempt(ctx_inject)

        assert inject_attempt.result == AuthResult.SUCCESS
        assert inst_page2.page.url.endswith("/dashboard")

        await inst_page2.close()
