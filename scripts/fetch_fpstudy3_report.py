"""Fetch the full report for the false-positive study job and dump details."""

from __future__ import annotations

import json

import httpx


def main() -> None:
    creds = json.load(open(".fpstudy3_job.json", encoding="utf-8"))
    c = httpx.Client(base_url="http://127.0.0.1:8001", timeout=60)
    h = {"Authorization": "Bearer " + creds["token"]}
    job_id = creds["job"]

    rep = c.get(f"/jobs/{job_id}/report", headers=h).json()

    print(f"URL: {rep['url']}")
    print(f"pages: {rep['pages_visited']} visited / {rep['pages_discovered']} discovered")
    print(f"endpoints: {rep['endpoints_discovered']}")
    print(f"quarantined: {rep['quarantined_issues']}")
    print(f"issues: {len(rep['issues'])}")
    print()

    for i in rep["issues"]:
        print("=" * 70)
        print(f"[{i['severity'].upper()}] [{i['category']}] {i['title']}")
        print(f"  desc: {i['description'][:200]}")
        print(f"  first_seen: {i['first_seen_url']}")
        print(f"  browsers: {i['browsers_affected']}")
        print(f"  occurrences: {i['occurrence_count']}")
        print(f"  gate_exempt: {i.get('gate_exempt')} flaky: {i.get('flaky_status')}")
        print(f"  error: {i['error_message'][:300]}")
        print(f"  fix_status: {i['fix_status']}")

    print()
    print("endpoints sample (first 30):")
    for e in rep["endpoints"][:30]:
        print(
            f"  [{e['status_code'] or '--'}] {e['method']} {e['url'][:90]} "
            f"(via {e['discovered_via']}, type={e['endpoint_type']})"
        )

    print()
    print("console error summary:", rep["console_error_summary"])
    print("error types:", rep["error_type_summary"])
    lh = rep.get("lighthouse_seed")
    if lh:
        print("lighthouse (first page):", json.dumps(lh, default=str)[:500])

    with open(".platform_report3.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, default=str)
    print("\nsaved .platform_report3.json")


if __name__ == "__main__":
    main()
