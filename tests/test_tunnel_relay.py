"""Tests for the tunnel protocol + relay core (no real sockets)."""

from __future__ import annotations

import asyncio

import pytest

from src.tunnel.protocol import (
    build_target_url,
    decode_frame,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
    filter_forward_headers,
    new_request_id,
)
from src.tunnel.relay import Relay


def test_request_response_roundtrip() -> None:
    rid = "abc123"
    frame = encode_request(rid, "POST", "/x/y", {"X-Foo": "bar"}, b"hello")
    data = decode_frame(frame)
    assert data["type"] == "request"
    req = decode_request(data)
    assert req["id"] == rid
    assert req["method"] == "POST"
    assert req["path"] == "/x/y"
    assert req["headers"] == {"X-Foo": "bar"}
    assert req["body"] == b"hello"


def test_response_roundtrip() -> None:
    frame = encode_response("rid", 200, {"Content-Type": "text/plain"}, b"ok")
    data = decode_frame(frame)
    resp = decode_response(data)
    assert resp["id"] == "rid"
    assert resp["status"] == 200
    assert resp["body"] == b"ok"


def test_filter_forward_headers_strips_hop_by_hop() -> None:
    h = {"Content-Type": "text/plain", "Host": "evil", "Connection": "keep-alive", "Content-Length": "10"}
    out = filter_forward_headers(h)
    assert "Content-Type" in out
    assert "Host" not in out
    assert "Connection" not in out
    assert "Content-Length" not in out


def test_build_target_url() -> None:
    assert build_target_url("http://localhost:3000", "/a/b") == "http://localhost:3000/a/b"
    assert build_target_url("http://localhost:3000/", "a/b") == "http://localhost:3000/a/b"
    assert build_target_url("http://localhost:3000", "") == "http://localhost:3000/"


class FakeConnection:
    """Echoing connection: send() enqueues a matching response into recv_queue,
    simulating an agent that answers each request frame."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.recv_queue: asyncio.Queue = asyncio.Queue()

    async def send(self, message: str) -> None:
        self.sent.append(message)
        frame = decode_frame(message)
        if frame.get("type") == "request":
            await self.recv_queue.put(encode_response(frame["id"], 200, {"Content-Type": "text/plain"}, b"pong"))

    async def recv(self) -> str:
        return await self.recv_queue.get()

    async def close(self) -> None:
        pass


def _echo_response(request_id: str, status: int = 200, body: bytes = b"pong") -> str:
    return encode_response(request_id, status, {"Content-Type": "text/plain"}, body)


def test_relay_request_roundtrip() -> None:
    async def run() -> None:
        relay = Relay()
        conn = FakeConnection()
        relay.register("tok1", conn)

        result = await relay.request("tok1", "GET", "/", {}, b"", timeout=5)
        assert result["status"] == 200
        assert result["body"] == b"pong"
        assert len(conn.sent) == 1
        relay.unregister("tok1")

    asyncio.run(run())


def test_relay_request_no_agent() -> None:
    async def run() -> None:
        relay = Relay()
        with pytest.raises(LookupError):
            await relay.request("missing", "GET", "/", {}, b"", timeout=1)

    asyncio.run(run())


def test_relay_sweep_drops_idle() -> None:
    async def run() -> None:
        relay = Relay(idle_timeout=0.01)
        conn = FakeConnection()
        relay.register("tok1", conn)
        await asyncio.sleep(0.05)  # monotonic clock advances past 10ms
        removed = relay.sweep()
        assert removed == 1
        assert not relay.is_connected("tok1")
        relay.unregister("tok1")

    asyncio.run(run())


def test_new_request_id_unique() -> None:
    assert new_request_id() != new_request_id()
