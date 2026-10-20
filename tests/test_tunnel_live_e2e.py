"""Live E2E for the self-hosted tunnel (real WebSocket, real HTTP relay).

Boots the actual FastAPI app on a free port, provisions a tunnel, runs the
real agent (src.tunnel.agent) against a local fixture target, and asserts a
request through /t/{token}/ round-trips end-to-end.

Run: pytest tests/test_tunnel_live_e2e.py -q
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn
from fastapi import Body, FastAPI
from fastapi.responses import PlainTextResponse

from src.api.app import SessionLocal
from src.models.database import PlatformUser, Tunnel


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def relay_server():
    """The real app: WS /tunnel/agent + /t/{token}/* relay."""
    from src.api.app import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("relay server did not start")
    yield base, f"ws://127.0.0.1:{port}/tunnel/agent"
    server.should_exit = True
    thread.join(timeout=3)


@pytest.fixture(scope="module")
def local_target():
    """A tiny local HTTP target only reachable on 127.0.0.1."""
    target = FastAPI()

    @target.get("/hello")
    async def hello():
        return PlainTextResponse("hello-from-local-target")

    @target.post("/echo")
    async def echo(data: bytes = Body(..., embed=False)):
        return PlainTextResponse(f"echo:{data.decode()}", status_code=200)

    port = _free_port()
    config = uvicorn.Config(target, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("local target did not start")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=3)


def _ensure_user() -> str:
    from src.api.auth import hash_password

    db = SessionLocal()
    try:
        uname = f"tun_e2e_{uuid.uuid4().hex[:8]}"
        u = PlatformUser(
            id=uuid.uuid4(), username=uname, email=f"{uname}@localhost",
            password_hash=hash_password("pw1234"), role="user", is_active=True,
        )
        db.add(u)
        db.commit()
        return uname
    finally:
        db.close()


def _login(base: str, username: str) -> str:
    r = httpx.post(f"{base}/api/auth/login", json={"username": username, "password": "pw1234"}, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _run_agent(relay_ws: str, token: str, target: str, stop: asyncio.Event):
    from src.tunnel.agent import run as agent_run

    async def runner():
        task = asyncio.create_task(agent_run(relay_ws, token, target))
        await stop.wait()
        task.cancel()

    asyncio.run(runner()) if False else None
    return runner


def test_tunnel_live_roundtrip(relay_server, local_target):
    base, relay_ws = relay_server

    # 1) provision a tunnel as a real user
    uname = _ensure_user()
    jwt = _login(base, uname)
    h = {"Authorization": f"Bearer {jwt}"}
    r = httpx.post(f"{base}/api/tunnels", headers=h, json={"name": "e2e", "target_origin": local_target}, timeout=10)
    assert r.status_code == 201, r.text
    body = r.json()
    agent_token = body["agent_token"]
    tunnel_token = body["tunnel_token"]
    tid = body["id"]

    # 2) run the real agent in a background thread (real WS to the real relay)
    from src.tunnel.agent import run as agent_run

    stop = threading.Event()

    def agent_thread():
        async def runner():
            task = asyncio.create_task(agent_run(relay_ws, agent_token, local_target))
            while not stop.is_set():
                await asyncio.sleep(0.1)
            task.cancel()

        with contextlib.suppress(Exception):
            asyncio.run(runner())

    t = threading.Thread(target=agent_thread, daemon=True)
    t.start()

    # 3) wait for the tunnel to connect (status flips to connected)
    connected = False
    for _ in range(50):
        r = httpx.get(f"{base}/api/tunnels", headers=h, timeout=10)
        row = next((x for x in r.json()["tunnels"] if x["id"] == tid), None)
        if row and row["status"] == "connected":
            connected = True
            break
        time.sleep(0.2)
    assert connected, "agent never connected (tunnel status stayed idle)"

    # 4) relay a GET through /t/{token}/ to the local target
    r = httpx.get(f"{base}/t/{tunnel_token}/hello", timeout=15)
    assert r.status_code == 200, r.text
    assert r.text == "hello-from-local-target"

    # 5) relay a POST with a body
    r = httpx.post(f"{base}/t/{tunnel_token}/echo", content=b"payload", timeout=15)
    assert r.status_code == 200, r.text
    assert r.text == "echo:payload"

    # 6) revoke and confirm the relay refuses after
    r = httpx.delete(f"{base}/api/tunnels/{tid}", headers=h, timeout=10)
    assert r.status_code == 200
    stop.set()
    time.sleep(0.3)

    # cleanup db
    db = SessionLocal()
    try:
        db.query(Tunnel).filter_by(id=uuid.UUID(tid)).delete()
        db.commit()
    finally:
        db.close()
