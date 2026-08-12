"""Install pytest in the existing Daytona sandbox and run the full smoke test."""
import os
from pathlib import Path

env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from daytona import Daytona, DaytonaConfig  # noqa: E402

SANDBOX_ID = "542e8307-6dcf-41da-8007-ff975f2db1d9"

print(f"Fetching sandbox {SANDBOX_ID} ...")
client = Daytona(DaytonaConfig(
    api_key=os.environ["DAYTONA_API_KEY"],
    api_url="https://app.daytona.io/api",
))
sandbox = client.get(SANDBOX_ID)
print(f"  state={sandbox.state} | snapshot={sandbox.snapshot}")

print("Installing pytest in sandbox (pip install pytest) ...")
r = sandbox.process.exec("pip install pytest -q 2>&1 | tail -3", timeout=180)
print(f"  install exit={r.exit_code}")
print(f"  install out={r.result!r}")

print("\nRunning pytest flow ...")
sandbox.fs.upload_file(b"def add(a, b):\n    return a + b\n", "/tmp/math_utils.py")
sandbox.fs.upload_file(
    b"from math_utils import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    "/tmp/test_math.py",
)
r2 = sandbox.process.exec(
    "cd /tmp && python3 -m pytest test_math.py -q --no-header --tb=short 2>&1",
    timeout=60,
)
print(f"  pytest exit_code={r2.exit_code}")
print(f"  pytest result={r2.result!r}")
assert r2.exit_code == 0, f"pytest failed: {r2.result}"
assert "1 passed" in (r2.result or ""), "expected 1 passed"
print("\nSMOKE TEST PASSED — Daytona sandbox runs pytest correctly.")