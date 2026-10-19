"""CI/CD helper: submit a crawl, wait for completion, gate the PR.

Designed for GitHub Actions (and any CI). Uses a CBTP API token
(``Authorization: Bearer cbtp_...``) — same auth path as normal users,
so per-user slot limits and tenant isolation apply.

Usage:
    python scripts/ci_gate.py --api-url https://cbtp.example.com \
        --token cbtp_... --target https://staging.example.com \
        [--max-blocking 0] [--max-functional 5] [--timeout 1800]

Exit codes:
    0  pass (crawl done, thresholds met)
    1  gate failed (thresholds exceeded)
    2  infrastructure error (submit/poll failed, timeout)
Results are printed as GitHub Actions annotations + a PR-ready summary.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx

TERMINAL = {"completed", "failed", "cancelled"}


def _resolve_project_id(c: httpx.Client, slug: str) -> str | None:
    """Resolve a project slug to its id via the projects list API."""
    r = c.get("/api/projects")
    if r.status_code != 200:
        return None
    for p in r.json().get("projects", []):
        if p.get("slug") == slug:
            return p["id"]
    return None


def run_visual_gate(
    c: httpx.Client,
    project_slug: str,
    job_id: str,
    branch: str | None,
    max_visual: int,
) -> dict[str, Any]:
    """Diff the completed job against the project baseline and return stats.

    Returns {project_id, compare_id, regressions, matches, new_baselines,
    total, results, skipped} where skipped=True when the project or diff
    endpoint is unavailable (infrastructure error, not a regression).
    """
    project_id = _resolve_project_id(c, project_slug)
    if project_id is None:
        die(2, f"project slug '{project_slug}' not found (or API list failed)")
    payload: dict[str, Any] = {"job_id": job_id}
    if branch:
        payload["branch"] = branch
    r = c.post(f"/api/projects/{project_id}/compare", json=payload)
    if r.status_code >= 500:
        die(2, f"compare failed ({r.status_code}): {r.text[:200]}")
    if r.status_code == 404:
        die(2, "compare endpoint unavailable (404) — is the projects API deployed?")
    if r.status_code != 201:
        die(2, f"compare failed ({r.status_code}): {r.text[:200]}")
    body = r.json()
    body["project_id"] = project_id
    return body


def print_visual_summary(vg: dict[str, Any]) -> None:
    print("\n===== CBTP Visual Diff Summary =====")
    print(f"Project:   {vg['project_id']}")
    print(f"Branch:    {vg.get('branch', 'default')}")
    print(f"Regressions: {vg['regressions']}")
    print(f"Matches:     {vg['matches']}")
    print(f"New baselines:{vg['new_baselines']}")
    for res in vg.get("results", []):
        if res.get("verdict") == "regression":
            pct = res.get("mismatch_percentage", 0.0)
            print(f"  - {res.get('url')} [{res.get('browser')}]: {pct:.4f}% changed")


def die(code: int, msg: str) -> None:
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(code)


def poll(base: httpx.Client, job_id: str, timeout_s: int) -> dict[str, Any]:
    started = time.monotonic()
    last_status = ""
    consecutive_errors = 0
    while time.monotonic() - started < timeout_s:
        try:
            r = base.get(f"/jobs/{job_id}")
            consecutive_errors = 0
        except httpx.TransportError as e:
            # Transient network blips (proxy restarts, LB idle drops) — tolerate up to 5.
            consecutive_errors += 1
            if consecutive_errors >= 5:
                die(2, f"status poll unreachable: {e}")
            time.sleep(5)
            continue
        if r.status_code != 200:
            die(2, f"status poll failed ({r.status_code}): {r.text[:200]}")
        job = r.json()
        status = job["status"]
        if status != last_status:
            print(f"[cbtp] job {job_id[:8]} -> {status} "
                  f"({job.get('pages_visited', 0)} pages, {job.get('issues_found', 0)} issues)")
            last_status = status
        if status in TERMINAL:
            return job
        time.sleep(5)
    die(2, f"timeout after {timeout_s}s waiting for job {job_id}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-url", required=True, help="CBTP base URL, e.g. https://cbtp.example.com")
    ap.add_argument("--token", required=True, help="CBTP API token (cbtp_...)")
    ap.add_argument("--target", required=True, help="URL to crawl (staging/preview deploy)")
    ap.add_argument("--max-blocking", type=int, default=0, help="Max BLOCKING issues allowed")
    ap.add_argument("--max-functional", type=int, default=-1, help="Max FUNCTIONAL issues allowed (-1 = no gate)")
    ap.add_argument("--timeout", type=int, default=1800, help="Seconds to wait for completion")
    ap.add_argument("--max-pages", type=int, default=50, help="Crawl budget for CI")
    ap.add_argument("--max-depth", type=int, default=3, help="Crawl depth for CI")
    ap.add_argument("--browsers", default="chromium", help="Comma list: chromium,firefox,webkit")
    # Visual-baseline diff gating (additive; requires --project).
    ap.add_argument("--project", default=None, help="Project slug to diff against (enables visual gate)")
    ap.add_argument("--branch", default=None, help="Branch of this run (defaults to the project's default_branch)")
    ap.add_argument("--max-visual", type=int, default=0, help="Max visual REGRESSIONS allowed (default 0)")
    args = ap.parse_args()

    matrix = {b.strip(): True for b in args.browsers.split(",") if b.strip()}
    full = {k: matrix.get(k, False) for k in ("chromium", "firefox", "webkit")}

    with httpx.Client(
        base_url=args.api_url.rstrip("/"),
        headers={"Authorization": f"Bearer {args.token}"},
        timeout=30.0,
    ) as c:
        # 1) sanity + auth check
        h = c.get("/api/me/crawler-slots")
        if h.status_code == 401:
            die(2, "API token rejected (401). Check CBTP_API_TOKEN secret.")
        if h.status_code != 200:
            die(2, f"cannot reach CBTP API ({h.status_code}).")
        slots = h.json()
        if not slots.get("canStartMore", True):
            die(2, "no free crawler slots on the CBTP account — clear CI jobs.")

        # 2) submit
        r = c.post(
            "/jobs",
            json={
                "url": args.target,
                "scope": "same-domain",
                "browser_matrix": full,
                "max_pages": args.max_pages,
                "max_depth": args.max_depth,
            },
        )
        if r.status_code == 429:
            die(2, "slot limit reached on CBTP account (429).")
        if r.status_code != 201:
            die(2, f"submit failed ({r.status_code}): {r.text[:300]}")
        job_id = r.json()["id"]
        print(f"[cbtp] submitted job {job_id}")

        # 3) wait
        job = poll(c, job_id, args.timeout)
        if job["status"] != "completed":
            die(2, f"crawl ended as '{job['status']}' — see {args.api_url}/jobs/{job_id}/dashboard")

        # 4) report + gate
        rep = c.get(f"/jobs/{job_id}/report")
        if rep.status_code != 200:
            die(2, f"report fetch failed ({rep.status_code}).")
        report = rep.json()

        sev = {"blocking": 0, "functional": 0, "visual": 0, "advisory": 0}
        by_cat: dict[str, int] = {}
        quarantined = 0
        for issue in report.get("issues", []):
            # Flaky quarantine: issues auto-quarantined (or human-confirmed flaky)
            # by cross-crawl history are counted separately and NEVER fail the gate.
            if issue.get("gate_exempt"):
                quarantined += 1
                continue
            sev[issue.get("severity", "advisory")] = sev.get(issue.get("severity", "advisory"), 0) + 1
            cat = issue.get("category", "unknown")
            by_cat[cat] = by_cat.get(cat, 0) + 1

        print("\n===== CBTP CI Gate Summary =====")
        print(f"Target:    {report['url']}")
        print(f"Pages:     {report['pages_visited']} visited / {report['pages_discovered']} discovered")
        print(f"Endpoints: {report['endpoints_discovered']}")
        print(f"Issues:    blocking={sev['blocking']} functional={sev['functional']} "
              f"visual={sev['visual']} advisory={sev['advisory']}")
        if quarantined:
            print(f"Quarantined (flaky, gate-exempt): {quarantined}")
        for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
            print(f"  - {cat}: {n}")
        print(f"Report:    {args.api_url}/jobs/{job_id}/dashboard")

        failed = False
        if sev["blocking"] > args.max_blocking:
            print(f"::error::CBTP gate: {sev['blocking']} blocking issues (max {args.max_blocking})")
            failed = True
        if args.max_functional >= 0 and sev["functional"] > args.max_functional:
            print(f"::error::CBTP gate: {sev['functional']} functional issues (max {args.max_functional})")
            failed = True

        # Visual-baseline gate (only when --project is provided).
        visual = None
        if args.project:
            visual = run_visual_gate(c, args.project, job_id, args.branch, args.max_visual)
            print_visual_summary(visual)
            if visual["regressions"] > args.max_visual:
                print(f"::error::CBTP visual gate: {visual['regressions']} regressions (max {args.max_visual})")
                failed = True

        print(json.dumps({
            "job_id": job_id,
            "blocking": sev["blocking"],
            "functional": sev["functional"],
            "pages": report["pages_visited"],
            "visual": {
                "regressions": visual["regressions"],
                "compare_id": visual.get("compare_id"),
            } if visual else None,
        }))
        sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
