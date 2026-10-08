"""CBTP stack orchestrator — embedded Postgres + Redis 8 + RQ workers + API + UI.

Reconstruction of the missing start-pg-stack.py based on stack.orchestrator.log
and _staging/redis8 binaries shipped in this package.

Design (from the original run log):
  - pgserver.get_server(pgdata-main) → embedded Postgres on a free port
  - rewrite .env: DATABASE_URL / DATABASE_URL_SYNC / REDIS_URL with live ports
  - start Redis 8 (from _staging/redis8) on 127.0.0.1:56379
  - start 2 RQ workers (src.workers.worker)
  - start API (uvicorn) on :8080
  - start UI (vite dev) on :3001 with VITE_API_URL=http://localhost:8080

Detached processes survive this script exiting.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGING = ROOT.parent  # _staging
LOG = ROOT / "stack.orchestrator.log"

REDIS_PORT = 56379
API_PORT = 8080
UI_PORT = 3001


def log(msg: str) -> None:
    line = f"[stack] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8", errors="replace") as f:
        f.write(line + "\n")


def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def rewrite_env(pg_url: str, redis_port: int) -> None:
    env_path = ROOT / ".env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    keep = [
        "DATABASE_URL",
        "DATABASE_URL_SYNC",
        "REDIS_URL",
    ]
    out = [ln for ln in lines if not any(ln.startswith(k + "=") for k in keep)]
    out.append(f"DATABASE_URL={pg_url.replace('postgresql://', 'postgresql+asyncpg://')}")
    out.append(f"DATABASE_URL_SYNC={pg_url}")
    out.append(f"REDIS_URL=redis://127.0.0.1:{redis_port}/0")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    log(f".env updated: DATABASE -> {pg_url}  REDIS -> 127.0.0.1:{redis_port}")


def start_redis() -> subprocess.Popen | None:
    redis_dir = STAGING / "redis8" / "Redis-8.10.0-Windows-x64-msys2"
    server = redis_dir / "redis-server.exe"
    if not server.exists():
        log(f"Redis binaries not found at {redis_dir} — skipping Redis")
        return None
    out = open(ROOT / "redis.out.log", "ab")
    err = open(ROOT / "redis.err.log", "ab")
    proc = subprocess.Popen(
        [
            str(server),
            "--port", str(REDIS_PORT),
            "--bind", "127.0.0.1",
            "--save", "",
            "--appendonly", "no",
            "--dir", str(redis_dir),
        ],
        cwd=redis_dir,
        stdout=out,
        stderr=err,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    log(f"Redis starting on :{REDIS_PORT}")
    return proc


def main() -> None:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("PYTHONPATH", str(ROOT))

    # 1. Embedded Postgres — cleanup_mode=None keeps it running after we exit;
    #    we then sleep forever so the stack stays supervised (Ctrl+C/Ctrl+Break stops it).
    import pgserver

    pgdata = ROOT / "pgdata-main"
    srv = pgserver.get_server(str(pgdata), cleanup_mode=None)
    pg_url = srv.get_uri()
    if "postgresql+asyncpg" in pg_url:
        pg_url_sync = pg_url.replace("+asyncpg", "")
    else:
        pg_url_sync = pg_url
    log(f"Postgres up: {pg_url_sync}")

    # 2. Rewrite .env with live ports
    rewrite_env(pg_url_sync, REDIS_PORT)

    # 3. Redis 8
    redis_proc = start_redis()
    if redis_proc is None:
        log("WARNING: no Redis — crawls will use in-process fallback")

    # 4. API
    api_out = open(ROOT / "api.out.log", "ab")
    api_err = open(ROOT / "api.err.log", "ab")
    api = subprocess.Popen(
        [
            sys.executable,
            "-m", "uvicorn", "src.api.app:app",
            "--host", "0.0.0.0", "--port", str(API_PORT),
        ],
        cwd=ROOT,
        stdout=api_out,
        stderr=api_err,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    log(f"API starting on :{API_PORT}")

    # 5. 2 RQ workers
    for i in (1, 2):
        w_out = open(ROOT / f"worker{i}.out.log", "ab")
        w_err = open(ROOT / f"worker{i}.err.log", "ab")
        subprocess.Popen(
            [sys.executable, "-m", "src.workers.worker"],
            cwd=ROOT,
            stdout=w_out,
            stderr=w_err,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    log("2 RQ workers started")

    # 6. UI (vite dev server)
    frontend = ROOT / "frontend"
    node_modules = frontend / "node_modules"
    ui = None
    if (frontend / "package.json").exists() and (node_modules).exists():
        ui_out = open(ROOT / "ui.out.log", "ab")
        ui_err = open(ROOT / "ui.err.log", "ab")
        ui = subprocess.Popen(
            [
                "cmd.exe", "/c",
                "npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", str(UI_PORT),
            ],
            cwd=frontend,
            stdout=ui_out,
            stderr=ui_err,
            env={
                **os.environ,
                "VITE_API_URL": f"http://localhost:{API_PORT}",
            },
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        log(f"UI starting on :{UI_PORT}")
    else:
        log("Frontend not installed — UI skipped")

    log(f"Stack is up. API :{API_PORT} | UI :{UI_PORT} | workers via RQ | Postgres embedded")

    import time

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log("Stack shutting down (KeyboardInterrupt)")


if __name__ == "__main__":
    main()