"""Pytest shared configurations and fixtures."""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
import time
from pathlib import Path

# ── Hermetic test environment (MUST run before any src import) ──────────────
# A developer's local .env may point at LIVE Postgres/Redis (e.g. Docker
# services on :5433/:6380). Without this guard, importing src.config during
# tests silently binds the shared `src.api.app` module to the live database —
# tests would read/write production data. Force every test to use isolated
# SQLite + MockRedis *before* the first src module import.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.environ["DATABASE_URL_SYNC"] = "sqlite:///./cbtp_test_suite.db"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./cbtp_test_suite.db"
os.environ["REDIS_URL"] = "redis://127.0.0.1:9/0"  # unreachable → MockRedis path
os.environ.setdefault("AUTH_DISABLED", "false")
os.environ.pop("JWT_SECRET", None)  # deterministic dev secret in tests
os.environ["SEED_USERS"] = "user1:user123:user1@localhost,user2:user123:user2@localhost"
os.environ.setdefault("MAX_CONCURRENT_JOBS_PER_USER", "2")

_TEST_DB = _PROJECT_ROOT / "cbtp_test_suite.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()

import pytest  # noqa: E402
import uvicorn  # noqa: E402

sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    yield
    # Dispose pooled connections FIRST — on Windows an open SQLite handle
    # makes the unlink below fail with WinError 32.
    try:
        from src.api.app import engine

        engine.dispose()
    except Exception:
        pass
    if _TEST_DB.exists():
        # A straggler connection may hold the file — non-fatal if unlink fails.
        with contextlib.suppress(OSError):
            _TEST_DB.unlink()


def find_free_port() -> int:
    """Find a free TCP port to launch the fixture app on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def fixture_app():
    """Fixture to start and stop the local fixture app in a background thread."""
    port = find_free_port()

    # Import the app inside the fixture to ensure paths are correct
    from fixture_app.app import app as fastapi_app

    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to spin up
    url = f"http://127.0.0.1:{port}"
    start_time = time.time()
    while time.time() - start_time < 5:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("Fixture app failed to start in time")

    yield url

    server.should_exit = True
    thread.join(timeout=2)
