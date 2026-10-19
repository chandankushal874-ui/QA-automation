"""Tunnel protocol: framed messages between relay and agent over one WS socket.

A single socket multiplexes many concurrent HTTP request/response exchanges.
Each exchange is identified by a ``request_id`` and carried in two frames:

    relay → agent   {type:"request", id, method, path, headers, body_b64}
    agent → relay   {type:"response", id, status, headers, body_b64}
    relay → agent   {type:"close", id}          (optional: cancel/cleanup)

body is base64-encoded to keep the JSON transport byte-safe. This module is
transport-agnostic — it only (de)serializes frames and builds an aiohttp
request from a frame + response from an aiohttp response, so both sides and the
tests share one source of truth.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from typing import Any


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def encode_frame(frame_type: str, **fields: Any) -> str:
    """Serialize one frame to a JSON string."""
    payload = {"type": frame_type, **fields}
    return json.dumps(payload, separators=(",", ":"))


def decode_frame(raw: str) -> dict[str, Any]:
    """Parse a JSON frame (raises ValueError on malformed input)."""
    data = json.loads(raw)
    if not isinstance(data, dict) or "type" not in data:
        raise ValueError("invalid frame: missing 'type'")
    return data


def encode_request(
    request_id: str,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
) -> str:
    return encode_frame(
        "request",
        id=request_id,
        method=method.upper(),
        path=path,
        headers=headers,
        body_b64=base64.b64encode(body).decode("ascii"),
    )


def decode_request(frame: dict[str, Any]) -> dict[str, Any]:
    """Normalize a request frame into a dict with decoded body bytes."""
    body_b64 = frame.get("body_b64", "")
    try:
        body = base64.b64decode(body_b64)
    except Exception:
        body = b""
    return {
        "id": frame.get("id"),
        "method": frame.get("method", "GET").upper(),
        "path": frame.get("path", "/"),
        "headers": frame.get("headers", {}) or {},
        "body": body,
    }


def encode_response(
    request_id: str,
    status: int,
    headers: dict[str, str],
    body: bytes,
) -> str:
    return encode_frame(
        "response",
        id=request_id,
        status=status,
        headers=headers,
        body_b64=base64.b64encode(body).decode("ascii"),
    )


def decode_response(frame: dict[str, Any]) -> dict[str, Any]:
    body_b64 = frame.get("body_b64", "")
    try:
        body = base64.b64decode(body_b64)
    except Exception:
        body = b""
    return {
        "id": frame.get("id"),
        "status": int(frame.get("status", 502)),
        "headers": frame.get("headers", {}) or {},
        "body": body,
    }


@dataclass
class PendingExchange:
    request_id: str
    future: Any = None
    created_at: float = 0.0

    @property
    def ready(self) -> bool:
        return self.future is not None and self.future.done()


def build_target_url(target_origin: str, path: str) -> str:
    """Join the agent's pinned target origin with an inbound path."""
    origin = (target_origin or "").rstrip("/")
    p = path or "/"
    if not p.startswith("/"):
        p = "/" + p
    return origin + p


# Headers that must never be forwarded upstream (hop-by-hop / relay-internal).
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
    "proxy-authorization",
    "te",
    "trailer",
    "host",
    "content-length",
}


def filter_forward_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip hop-by-hop + host/content-length headers before forwarding."""
    return {
        k: v
        for k, v in (headers or {}).items()
        if k.lower() not in HOP_BY_HOP
    }


__all__ = [
    "PendingExchange",
    "build_target_url",
    "decode_frame",
    "decode_request",
    "decode_response",
    "encode_frame",
    "encode_request",
    "encode_response",
    "filter_forward_headers",
    "new_request_id",
]
