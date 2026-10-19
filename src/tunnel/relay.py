"""Tunnel relay: authenticate agents and multiplex inbound HTTP over their WS.

Design (testable without real sockets): the relay core depends on a thin
``Connection`` protocol (send_frame / recv_frame / close), not on websockets
directly. The FastAPI layer adapts a Starlette WebSocket to that interface.

Concurrency model: each authenticated session runs ONE reader task that pulls
frames off the socket and resolves per-request futures by ``request_id``. This
lets many inbound HTTP requests multiplex over a single agent socket without
frames being stolen across requests.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from src.tunnel.protocol import (
    decode_frame,
    decode_response,
    encode_request,
    filter_forward_headers,
    new_request_id,
)

logger = logging.getLogger(__name__)

DEFAULT_IDLE_TIMEOUT = 1800.0
DEFAULT_REQUEST_TIMEOUT = 60.0


class Connection(Protocol):
    """Minimal async message channel the relay needs (websockets-agnostic)."""

    async def send(self, message: str) -> None: ...
    async def recv(self) -> str: ...
    async def close(self) -> None: ...


@dataclass
class TunnelSession:
    tunnel_token: str
    connection: Connection
    connected_at: float = 0.0
    last_active_at: float = 0.0
    _pending: dict[str, asyncio.Future] = None  # type: ignore[assignment]
    _reader: asyncio.Task | None = None
    _closed: bool = False

    def __post_init__(self) -> None:
        self._pending = {}

    def touch(self) -> None:
        self.last_active_at = time.monotonic()


class Relay:
    """Maps tunnel tokens → live agent connections and relays requests."""

    def __init__(self, idle_timeout: float = DEFAULT_IDLE_TIMEOUT) -> None:
        self._sessions: dict[str, TunnelSession] = {}
        self._idle_timeout = idle_timeout

    def register(self, tunnel_token: str, connection: Connection) -> TunnelSession:
        """Attach a freshly-authenticated agent socket and start its reader."""
        session = TunnelSession(tunnel_token=tunnel_token, connection=connection, connected_at=time.monotonic())
        session.touch()
        self._sessions[tunnel_token] = session
        session._reader = asyncio.create_task(self._reader_loop(session))
        return session

    def unregister(self, tunnel_token: str) -> None:
        session = self._sessions.pop(tunnel_token, None)
        if session is not None:
            session._closed = True
            for fut in session._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("tunnel session closed"))
            if session._reader and not session._reader.done():
                session._reader.cancel()

    def is_connected(self, tunnel_token: str) -> bool:
        return tunnel_token in self._sessions

    def sweep(self) -> int:
        now = time.monotonic()
        dead = [tok for tok, s in self._sessions.items() if now - s.last_active_at > self._idle_timeout]
        for tok in dead:
            self.unregister(tok)
        return len(dead)

    def active_count(self) -> int:
        return len(self._sessions)

    async def _reader_loop(self, session: TunnelSession) -> None:
        """Pull frames off the socket and resolve the matching future."""
        try:
            while not session._closed:
                raw = await session.connection.recv()
                try:
                    frame = decode_frame(raw)
                except ValueError:
                    continue
                if frame.get("type") != "response":
                    continue
                rid = frame.get("id")
                fut = session._pending.pop(rid, None)
                if fut is not None and not fut.done():
                    fut.set_result(decode_response(frame))
                session.touch()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("tunnel reader loop ended for %s: %s", session.tunnel_token, e)
        finally:
            for _rid, fut in list(session._pending.items()):
                if not fut.done():
                    fut.set_exception(ConnectionError("agent disconnected"))

    async def request(
        self,
        tunnel_token: str,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> dict[str, Any]:
        """Relay one HTTP request over the agent's socket and await the response.

        Returns {status, headers, body}. Raises LookupError if the agent is not
        connected and TimeoutError if no response arrives in time.
        """
        session = self._sessions.get(tunnel_token)
        if session is None:
            raise LookupError(f"no agent connected for tunnel '{tunnel_token}'")

        request_id = new_request_id()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        session._pending[request_id] = fut
        session.touch()

        frame = encode_request(request_id, method, path, filter_forward_headers(headers), body)
        try:
            await session.connection.send(frame)
            response = await asyncio.wait_for(fut, timeout=timeout)
            return response
        except TimeoutError as e:
            session._pending.pop(request_id, None)
            raise TimeoutError(f"tunnel response {request_id} timed out") from e
        except Exception:
            session._pending.pop(request_id, None)
            raise


def make_default_relay() -> Relay:
    return Relay()


__all__ = ["Connection", "Relay", "TunnelSession", "make_default_relay"]
