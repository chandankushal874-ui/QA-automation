"""Pytest shared configurations and fixtures."""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn


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
