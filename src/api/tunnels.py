"""Tunnels API — provision/manage self-hosted reverse tunnels + relay WS.

Owner-scoped like every other resource. The relay mounts:
    WS  /tunnel/agent/{token}      agent authenticates + connects
    ANY /t/{tunnel_token}/{path}   inbound crawl request → agent → local target

The agent authenticates with a full ``cbtp_...`` API token; the relay matches
its SHA-256 against the tunnels table (reusing the token system, so revoking a
token also kills its tunnel).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.websockets import WebSocket

from src.api.auth import get_current_user, get_db_dep
from src.models.database import PlatformUser, Tunnel, UserRole
from src.tunnel.relay import Connection, Relay, make_default_relay

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tunnels", tags=["tunnels"])

# Process-wide relay singleton (overridden in tests via set_relay).
_relay: Relay = make_default_relay()


def set_relay(r: Relay) -> None:
    global _relay
    _relay = r


def get_relay() -> Relay:
    return _relay


TUNNEL_IDLE_MINUTES = 30


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _gen_tunnel_token() -> str:
    return secrets.token_hex(16)


def _tunnel_dict(t: Tunnel) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "name": t.name,
        "tunnel_token": t.tunnel_token,
        "target_origin": t.target_origin,
        "status": t.status,
        "target_url": f"/t/{t.tunnel_token}/",
        "expires_at": t.expires_at.isoformat() if t.expires_at else None,
        "last_active_at": t.last_active_at.isoformat() if t.last_active_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _get_owned_tunnel(db: Session, tunnel_id: str, user: PlatformUser) -> Tunnel:
    try:
        tid = uuid.UUID(tunnel_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Tunnel not found") from None
    t = db.query(Tunnel).filter_by(id=tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    if user.role != UserRole.ADMIN.value and str(t.owner_id) != str(user.id):
        raise HTTPException(status_code=404, detail="Tunnel not found")
    return t


class CreateTunnelRequest(BaseModel):
    name: str
    target_origin: str = ""


@router.post("", status_code=201)
async def create_tunnel(
    req: CreateTunnelRequest,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    """Provision a tunnel. Returns a one-time API token for the agent."""
    raw_token = "cbtp_" + secrets.token_hex(16)
    tunnel = Tunnel(
        owner_id=user.id,
        name=req.name.strip() or "tunnel",
        tunnel_token=_gen_tunnel_token(),
        token_hash=_hash(raw_token),
        target_origin=req.target_origin,
        status="idle",
        expires_at=datetime.now(UTC) + timedelta(minutes=TUNNEL_IDLE_MINUTES),
    )
    db.add(tunnel)
    db.commit()
    db.refresh(tunnel)
    body = _tunnel_dict(tunnel)
    body["agent_token"] = raw_token  # shown once
    return body


@router.get("")
async def list_tunnels(
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    q = db.query(Tunnel)
    if user.role != UserRole.ADMIN.value:
        q = q.filter(Tunnel.owner_id == user.id)
    rows = q.order_by(Tunnel.created_at.desc()).limit(500).all()
    return {"tunnels": [_tunnel_dict(t) for t in rows], "total": len(rows)}


@router.delete("/{tunnel_id}")
async def revoke_tunnel(
    tunnel_id: str,
    user: PlatformUser = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
) -> dict[str, Any]:
    t = _get_owned_tunnel(db, tunnel_id, user)
    _relay.unregister(t.tunnel_token)
    t.status = "revoked"
    db.commit()
    return {"status": "revoked", "tunnel_id": str(t.id)}


# ── Relay (mounted as part of the FastAPI app) ──────────────────────────────


class _WSConnection(Connection):
    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def send(self, message: str) -> None:
        await self._ws.send_text(message)

    async def recv(self) -> str:
        return await self._ws.receive_text()

    async def close(self) -> None:
        await self._ws.close()


async def agent_ws(ws: WebSocket, db: Session) -> None:
    """Authenticate an agent (Bearer header) and keep its socket registered.

    The relay's per-session reader task is the SOLE consumer of inbound frames;
    this handler must not receive() concurrently. It stays alive until the
    reader ends (disconnect) and signals via the ``closed`` event.
    """
    await ws.accept()

    auth = ws.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    tunnel = None
    if token:
        token_hash = _hash(token)
        tunnel = db.query(Tunnel).filter_by(token_hash=token_hash).first()
    if not tunnel:
        await ws.send_text('{"type":"error","detail":"invalid token"}')
        await ws.close(code=4401)
        return

    tunnel.status = "connected"
    tunnel.last_active_at = datetime.now(UTC)
    db.commit()

    closed = asyncio.Event()
    conn = _WSConnection(ws)
    _relay.register(tunnel.tunnel_token, conn, on_closed=closed.set)
    try:
        # Idle until the relay reader detects disconnect and signals us.
        await closed.wait()
    except Exception:
        pass
    finally:
        _relay.unregister(tunnel.tunnel_token)
        # Short-lived session for the status flip (the handler's session is
        # released with the response lifecycle).
        try:
            from src.api.app import SessionLocal

            s2 = SessionLocal()
            try:
                row = s2.query(Tunnel).filter_by(id=tunnel.id).first()
                if row is not None and row.status == "connected":
                    row.status = "idle"
                    s2.commit()
            finally:
                s2.close()
        except Exception:
            pass


async def relay_http(tunnel_token: str, request: Request, path: str, db: Session) -> Response:
    """Forward an inbound HTTP request to the agent for this tunnel."""
    tunnel = db.query(Tunnel).filter_by(tunnel_token=tunnel_token).first()
    if not tunnel:
        raise HTTPException(status_code=404, detail="tunnel not found")

    headers = dict(request.headers.items())
    body = await request.body()
    try:
        result = await _relay.request(
            tunnel.tunnel_token,
            request.method,
            "/" + path,
            headers,
            body,
        )
    except LookupError:
        raise HTTPException(status_code=503, detail="tunnel agent not connected") from None
    except TimeoutError:
        raise HTTPException(status_code=504, detail="tunnel agent timed out") from None

    resp_headers = {k: v for k, v in result["headers"].items() if k.lower() not in {"content-length", "connection", "transfer-encoding"}}
    return Response(
        content=result["body"],
        status_code=result["status"],
        headers=resp_headers,
    )
