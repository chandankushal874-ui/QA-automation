"""Poll the false-positive study job until terminal status."""

from __future__ import annotations

import json
import sys
import time

import httpx


def main() -> int:
    creds = json.load(open(".fpstudy2_job.json", encoding="utf-8"))
    c = httpx.Client(base_url="http://127.0.0.1:8001", timeout=30)
    h = {"Authorization": "Bearer " + creds["token"]}
    job_id = creds["job"]

    for _ in range(120):
        j = c.get(f"/jobs/{job_id}", headers=h).json()
        print(
            f"status={j['status']} pages={j['pages_visited']}/{j['pages_discovered']} "
            f"issues={j['issues_found']} endpoints={j['endpoints_discovered']}",
            flush=True,
        )
        if j["status"] in ("completed", "failed", "cancelled"):
            return 0
        time.sleep(15)
    print("timeout waiting for job")
    return 1


if __name__ == "__main__":
    sys.exit(main())
