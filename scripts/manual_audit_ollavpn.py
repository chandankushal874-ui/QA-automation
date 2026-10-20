"""Independent manual audit of ollavpn.com (comparison baseline).

Not using the platform — plain httpx + UA, checking:
  1. Status of every same-site link on the homepage
  2. Broken static assets (js/css/img/fonts) referenced by the homepage
  3. Page titles / basic SEO signals
  4. Security headers on key pages
  5. Redirect chains

Run: python -X utf8 scripts/manual_audit_ollavpn.py
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

import httpx

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}

BASE = "https://ollavpn.com"


def main() -> None:
    c = httpx.Client(headers=UA, timeout=20, follow_redirects=True)

    r = c.get(BASE)
    print(f"home: {r.status_code} {len(r.text)} bytes")
    html = r.text

    # 1) every same-site link on the homepage
    raw_links = set(re.findall(r'href="(https?://[^"]+|/[^"]*)"', html))
    abs_links = sorted({urljoin(BASE, l).split("#")[0] for l in raw_links if l})
    same = [l for l in abs_links if l.startswith(BASE)]
    print(f"same-site links on home: {len(same)}")

    bad_pages: list[tuple[str, str]] = []
    statuses: dict[str, int] = {}
    for url in same[:40]:
        try:
            rr = c.head(url)
            if rr.status_code in (403, 405):
                rr = c.get(url)
            statuses[url] = rr.status_code
            if rr.status_code >= 400:
                bad_pages.append((url, str(rr.status_code)))
        except Exception as e:  # noqa: BLE001
            statuses[url] = -1
            bad_pages.append((url, f"ERR {type(e).__name__}"))
    print(f"pages checked: {len(statuses)} — bad: {len(bad_pages)}")
    for u, s in bad_pages:
        print(f"  BAD PAGE: {u} -> {s}")

    # 2) static assets referenced by the homepage
    asset_urls = re.findall(r'(?:src|href)="(/[^"\']+\.(?:js|css|png|jpg|jpeg|svg|ico|woff2?))"', html)
    broken_assets: list[tuple[str, int]] = []
    for a in sorted(set(asset_urls))[:25]:
        rr = c.get(BASE + a)
        if rr.status_code >= 400:
            broken_assets.append((a, rr.status_code))
    print(f"broken assets: {broken_assets}")

    # 3) title/description on key pages
    key_pages = ["/", "/pricing", "/technology", "/pqc", "/free-vpn", "/contact", "/privacy", "/terms", "/dl/"]
    print("titles:")
    for p in key_pages:
        try:
            rr = c.get(BASE + p)
            m = re.search(r"<title[^>]*>(.*?)</title>", rr.text, re.S)
            desc = re.search(r'name="description" content="([^"]*)"', rr.text)
            t = (m.group(1).strip()[:70] if m else "MISSING")
            print(f"  {p} [{rr.status_code}] title={t!r} desc={'yes' if desc else 'MISSING'}")
        except Exception as e:  # noqa: BLE001
            print(f"  {p} ERR {type(e).__name__}")

    # 4) security headers
    print("security headers (home):")
    for h in ["strict-transport-security", "content-security-policy", "x-frame-options",
              "x-content-type-options", "referrer-policy"]:
        print(f"  {h}: {r.headers.get(h, 'MISSING')}")

    # 5) redirect chains from nav links to other subdomains
    print("cross-domain nav links:")
    for l in abs_links:
        if not l.startswith(BASE):
            print(f"  {l}")

    # 6) mixed content: http:// resources on https page
    mixed = re.findall(r'(?:src|href)="(http://[^"]+)"', html)
    print(f"mixed-content refs: {mixed[:5] if mixed else 'none'}")

    out = {
        "statuses": statuses,
        "bad_pages": bad_pages,
        "broken_assets": broken_assets,
        "mixed_content": mixed,
        "headers": {h: r.headers.get(h) for h in
                     ["strict-transport-security", "content-security-policy", "x-frame-options",
                      "x-content-type-options", "referrer-policy"]},
    }
    with open(".my_audit.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("saved .my_audit.json")


if __name__ == "__main__":
    main()