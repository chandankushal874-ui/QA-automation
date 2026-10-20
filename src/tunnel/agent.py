"""Tunnel agent — runs on the developer's machine or in CI.

Maintains one outbound WebSocket to the platform relay and, for each inbound
request frame, performs the HTTP request against the pinned local target and
returns the response frame. Uses aiohttp (already a dependency) for the target
requests and ``websockets`` for the outbound socket.

Run:
    python -m src.tunnel.agent --relay wss://cbtp.example.com/tunnel/agent \\
        --token cbtp_... --target http://localhost:3000
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

import aiohttp
import websockets

from src.tunnel.protocol import (
    decode_frame,
    decode_request,
    encode_response,
)

logger = logging.getLogger(__name__)


async def handle_frame(ws: Any, frame: str, target_origin: str, session: aiohttp.ClientSession) -> None:
    """Process one inbound frame and send the matching response frame."""
    data = decode_frame(frame)
    if data.get("type") != "request":
        return
    req = decode_request(data)
    request_id = req["id"]
    url = (target_origin or "").rstrip("/") + (req["path"] or "/")

    try:
        async with session.request(
            method=req["method"],
            url=url,
            headers=req["headers"],
            data=req["body"] or None,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            body = await resp.read()
            headers = dict(resp.headers.items())
            await ws.send(encode_response(request_id, resp.status, headers, body))
    except Exception as e:
        logger.warning("target request failed for %s: %s", url, e)
        await ws.send(encode_response(request_id, 502, {"x-tunnel-error": str(e)[:200]}, b""))


async def run(relay_url: str, token: str, target_origin: str) -> None:
    """Open the outbound WS and serve requests until the connection drops."""
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with websockets.connect(
                    relay_url, additional_headers={"Authorization": f"Bearer {token}"}
                ) as ws:
                    logger.info("tunnel agent connected to %s", relay_url)
                    async for message in ws:
                        await handle_frame(ws, message, target_origin, session)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("agent connection lost (%s) — reconnecting in 3s", e)
                await asyncio.sleep(3)


def main() -> None:
    ap = argparse.ArgumentParser(description="CBTP self-hosted tunnel agent")
    ap.add_argument("--relay", required=True, help="Relay WS URL, e.g. wss://host/tunnel/agent")
    ap.add_argument("--token", required=True, help="CBTP agent token (cbtp_...)")
    ap.add_argument("--target", required=True, help="Local target origin, e.g. http://localhost:3000")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run(args.relay, args.token, args.target))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
