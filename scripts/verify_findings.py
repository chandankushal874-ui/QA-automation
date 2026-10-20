"""Verify each platform finding against the live site, one by one.

Reads .platform_report.json and re-checks every issue's core claim with
plain HTTP, then writes a verdict: TRUE POSITIVE / FALSE POSITIVE / PARTIAL.
"""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import unquote

import httpx

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}


def main() -> None:
    rep = json.load(open(".platform_report.json", encoding="utf-8"))
    c = httpx.Client(headers=UA, timeout=20, follow_redirects=True)

    print(f"verdicts for {len(rep['issues'])} platform findings:\n")

    # ---- verify the sitemap-declared URLs myself (including the &amp; ones)
    sitemap = c.get("https://ollavpn.com/sitemap.xml").text
    declared = re.findall(r"<loc>(.*?)</loc>", sitemap)
    declared_unescaped = [unescape(u) for u in declared]
    print(f"sitemap declares {len(declared)} URLs\n")

    checked: dict[str, int] = {}
    verdicts = []

    for issue in rep["issues"]:
        title = issue["title"]
        url = issue["first_seen_url"]
        cat = issue["category"]
        sev = issue["severity"]
        err = issue["error_message"]

        # --- sitemap-404 findings
        if "Sitemap URL returns 404" in title:
            # Re-request exactly what the platform requested
            rr = c.get(url)
            # Also: is the URL actually declared in the sitemap (as-is)?
            declared_raw = url in declared
            declared_after_unescape = unquote(url) in [unquote(u) for u in declared_unescaped]
            exact_in_sitemap = url in declared_unescaped
            verdict = "TRUE POSITIVE" if rr.status_code == 404 else "FALSE POSITIVE"
            notes = f"recheck={rr.status_code} in_sitemap_as_is={declared_raw} after_unescape={exact_in_sitemap}"
            if rr.status_code == 404 and not exact_in_sitemap:
                verdict = "PARTIAL — 404 real but URL not exactly in sitemap (encoding bug)"
            checked[url] = rr.status_code
            print(f"[{verdict}] [{sev}] sitemap404: {url}")
            print(f"    {notes}\n")
            verdicts.append((title, verdict))
            continue

        # --- security-probe findings (exposed paths)
        if cat == "security":
            path = url.replace("https://ollavpn.com", "")
            rr = c.get("https://ollavpn.com" + path)
            exposed = rr.status_code == 200
            # These files are *meant* to be public — judge by intent
            intentional = path in (
                "/robots.txt", "/sitemap.xml", "/humans.txt", "/llms.txt",
                "/.well-known/security.txt", "/favicon.ico", "/status",
            )
            verdict = (
                "FALSE POSITIVE (intentional public file)"
                if exposed and intentional
                else f"TRUE POSITIVE? status={rr.status_code} intentional={intentional}"
            )
            print(f"[{verdict}] [{sev}] security: {path} -> {rr.status_code}\n")
            verdicts.append((title, verdict))
            continue

        # --- console errors
        if cat == "console_error":
            print(f"[MANUAL REVIEW] [{sev}] console: {title[:90]}")
            print(f"    raw error: {err[:200]}\n")
            verdicts.append((title, "MANUAL — console error, needs browser repro"))
            continue

        print(f"[UNHANDLED] {cat}: {title[:90]}\n")
        verdicts.append((title, "UNHANDLED"))

    # ---- summary
    print("=" * 70)
    tp = sum(1 for _, v in verdicts if v.startswith("TRUE"))
    fp = sum(1 for _, v in verdicts if v.startswith("FALSE"))
    partial = sum(1 for _, v in verdicts if v.startswith("PARTIAL"))
    manual = sum(1 for _, v in verdicts if v.startswith("MANUAL"))
    print(f"total findings: {len(verdicts)}")
    print(f"  true positives: {tp}")
    print(f"  false positives: {fp}")
    print(f"  partial (platform bug): {partial}")
    print(f"  manual review: {manual}")
    print(f"  false-positive rate: {round(fp / len(verdicts) * 100, 1)}%" if verdicts else "")


if __name__ == "__main__":
    main()