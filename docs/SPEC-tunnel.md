# Spec: Self-Hosted Tunnel (local/staging → crawler)

## Objective

Expose a target that is only reachable on the developer's machine or CI
(localhost, an unexposed staging box, a Vercel preview behind SSO) to the
platform's crawler, so it can be tested without a public URL. Self-hosted: a
lightweight **tunnel agent** the user runs, which maintains a single **outbound**
WebSocket to the platform; the platform reverse-proxies inbound crawl traffic
back over that socket to the agent, which forwards it to the local target.

## Architecture

```
[crawler] ──HTTP──▶ [platform tunnel relay] ──WS──▶ [tunnel agent] ──HTTP──▶ [local target]
                       (assigns stable host/             (runs on user machine)
                        path per tunnel)
```

- **Agent** (`tunnel/agent.py`): a standalone Python process (no Playwright dep).
  Authenticates with an API token (`cbtp_...`), opens one outbound WebSocket to
  the relay, and for each inbound request multiplexed over the socket, forwards
  it to the local target and streams the response back.
- **Relay** (`tunnel/relay.py`): an ASGI/uvicorn service (same FastAPI app,
  mounted at `/tunnel`) that (a) authenticates agents, (b) assigns each tunnel a
  stable token → `https://<host>/t/<token>/...` route, (c) maps inbound HTTP to
  an open agent socket and relays the bytes.
- **Lifecycle**: tunnels are **ephemeral by default** (idle TTL, e.g. 30 min).
  A `POST /api/tunnels` provisions a token; the agent connects with it; crawls
  target `https://<host>/t/<token>/`.

## Security (hard requirements)

- Agents authenticate with a **hashed API token** (reuse the existing token
  system) — never an unauthenticated open relay.
- A tunnel token is single-use per active session and **scoped to the owner**;
  the relay refuses to route to an agent socket it didn't authorize.
- Relay enforces a max concurrent tunnels per owner and an idle timeout.
- The agent only dials the single configured target origin (no open proxy);
  the relay pins the request to that tunnel's agent socket.

## v1 scope (honest)

- HTTP/1.1 only (no WebSocket *upgrade* pass-through, no streaming/chunked
  uploads) — covers 95% of crawler traffic (GET/HEAD/POST forms).
- Single relay process in the platform deployment (the crawler already reaches
  it on the internal network via `http://<host>/t/<token>`).
- Agent is a `pip install`-able module + `python -m src.tunnel.agent` entrypoint
  (works on dev machine and in CI as a background step).

## Non-goals (v1)

- No auto-provisioned DNS/subdomains (uses path-based `/t/<token>/` routing).
- No TCP/raw forwarding, no HTTPS termination for arbitrary hostnames.

## API surface

```
POST /api/tunnels                 provision a tunnel (returns tunnel_token, target_url)
GET  /api/tunnels                 list own tunnels (active/expired)
DELETE /api/tunnels/{id}          revoke
# Relay (internal, mounted in app.py):
WS   /tunnel/agent/{token}        agent connects (authenticated)
ANY  /t/<token>/{path...}         inbound crawl request → agent → target
```

## Files

```
src/tunnel/__init__.py      NEW
src/tunnel/relay.py         NEW (ASGI relay + WS auth + request mux)
src/tunnel/agent.py         NEW (outbound agent, forwards to local target)
src/api/tunnels.py          NEW (provision/list/revoke, owner-scoped)
src/models/database.py      EXTEND (Tunnel model: owner_id, token_hash, target_origin, status, expires_at)
src/api/app.py              EXTEND (mount relay + /api/tunnels)
docker-compose.prod.yml     EXTEND (optional relay service / port)
tests/test_tunnels_api.py   NEW
tests/test_tunnel_relay.py  NEW (agent↔relay loopback over a fake socket)
```

## Success criteria

1. `POST /api/tunnels` returns a token; an agent connects with it and the relay
   accepts the socket.
2. A request to `/t/<token>/<path>` is forwarded by the agent to the local
   target and the response is returned to the caller (integration test with a
   local loopback target).
3. An agent with a bad/expired token is rejected (401/403).
4. Owner isolation: User A cannot see/route User B's tunnel.
5. A crawl of `https://<host>/t/<token>/` succeeds end-to-end against a local
   fixture app (E2E).
6. Full suite + ruff green.
