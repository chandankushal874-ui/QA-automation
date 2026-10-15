"""Daytona SDK smoke test.

Verifies the API surface used by fix_engine.py:
  - Daytona(DaytonaConfig(api_key, api_url))
  - daytona.create(CreateSandboxFromSnapshotParams(snapshot, language, ...))
  - sandbox.fs.upload_file(bytes, remote_path)
  - sandbox.process.exec(command, timeout=...) -> ExecuteResponse(exit_code, result)
  - sandbox.delete()
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Load .env manually (independent of the app config)
env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

api_key = os.environ.get("DAYTONA_API_KEY", "")
api_url = os.environ.get("DAYTONA_API_URL", "https://app.daytona.io/api")
if not api_key:
    print("FAIL: DAYTONA_API_KEY not set")
    sys.exit(1)

from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig  # noqa: E402

print(f"Connecting to Daytona at {api_url} ...")
config = DaytonaConfig(api_key=api_key, api_url=api_url)
client = Daytona(config)

print("Creating sandbox (daytona-small, python) ...")
t0 = time.time()
sandbox = client.create(
    CreateSandboxFromSnapshotParams(
        snapshot="daytona-small",
        language="python",
        auto_stop_interval=0,
        auto_delete_interval=0,
    )
)
print(f"  sandbox ready in {time.time() - t0:.2f}s")

try:
    # 1. upload_file with bytes
    payload = b"print('hello from daytona smoke test')\n"
    remote_path = "/tmp/smoke.py"
    print(f"Uploading bytes to {remote_path} ...")
    sandbox.fs.upload_file(payload, remote_path)
    print("  upload OK")

    # 2. process.exec -> ExecuteResponse
    print("Running python3 /tmp/smoke.py ...")
    result = sandbox.process.exec("python3 /tmp/smoke.py", timeout=60)
    exit_code = getattr(result, "exit_code", None)
    out = getattr(result, "result", None)
    print(f"  exit_code={exit_code!r}")
    print(f"  result={out!r}")

    if exit_code != 0:
        print(f"FAIL: expected exit_code 0, got {exit_code}")
        sys.exit(2)
    if "hello from daytona smoke test" not in (out or ""):
        print(f"FAIL: expected greeting in output, got {out!r}")
        sys.exit(3)
    print("PASS: exec + upload_file + ExecuteResponse.result all work")

finally:
    print("Deleting sandbox ...")
    try:
        sandbox.delete()
        print("  deleted OK")
    except Exception as e:
        print(f"  delete failed: {e}")

print("SMOKE TEST PASSED")
