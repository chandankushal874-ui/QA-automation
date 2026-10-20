"""Trace where the platform's phantom tag URLs came from."""

from __future__ import annotations

import re
from collections import Counter

import httpx

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}


def main() -> None:
    c = httpx.Client(headers=UA, timeout=20, follow_redirects=True)

    # Crawl-state rows from the platform report tell us discovered_via;
    # check the blog index HTML for the raw hrefs.
    rep = json.load(open(".platform_report.json", encoding="utf-8")) if False else None

    html = c.get("https://ollavpn.com/blog/").text
    hits = re.findall(r'href="([^"]*tags[^"]*)"', html)
    print(f"tag hrefs on /blog/: {len(hits)} (unique: {len(set(hits))})")
    for h, n in Counter(hits).most_common(15):
        print(f"  {n}x {h}")

    print()
    amp = [h for h in set(hits) if "&amp;" in h or "&" in h]
    print(f"hrefs containing entities/ampersands: {amp[:6]}")
    cap = [h for h in set(hits) if re.search(r"/tags/[A-Z]", h)]
    print(f"capitalized tag hrefs: {cap[:6]}")

    # Do the SAME mis-unescaped URLs appear in the platform's endpoint list?
    import json

    rep = json.load(open(".platform_report.json", encoding="utf-8"))
    eps = [e["url"] for e in rep["endpoints"]]
    phantom = [
        u for u in eps
        if "/tags/" in u and (re.search(r"/tags/[A-Z]", u) or "&amp;" in u or "%20" in u)
    ]
    print(f"\nplatform endpoints with phantom tag URLs: {len(phantom)}")
    for u in sorted(set(phantom)):
        print(f"  {u}")
    via = [e for e in rep["endpoints"] if e["url"] in phantom]
    for e in via[:8]:
        print(f"  {e['url'][:80]} via={e['discovered_via']} status={e['status_code']}")

    # Sitemap-declared tag URLs for contrast
    sm = c.get("https://ollavpn.com/sitemap.xml").text
    locs = re.findall(r"<loc>(.*?)</loc>", sm)
    print(f"\nsitemap tag URLs (real, all lowercase): {[l for l in locs if '/tags/' in l]}")


if __name__ == "__main__":
    main()