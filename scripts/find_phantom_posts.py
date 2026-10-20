"""Find which blog posts contain the phantom (capitalized / &amp;) tag links."""

from __future__ import annotations

import re

import httpx

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}

PHANTOM = re.compile(r'href="([^"]*(?:/tags/[A-Z]|%20|&amp;)[^"]*)"')


def main() -> None:
    c = httpx.Client(headers=UA, timeout=20, follow_redirects=True)
    sm = c.get("https://ollavpn.com/sitemap.xml").text
    posts = [u for u in re.findall(r"<loc>(.*?)</loc>", sm) if "/blog/" in u and u.rstrip("/") != "https://ollavpn.com/blog"]
    print(f"blog posts in sitemap: {len(posts)}")

    found: list[tuple[str, list[str]]] = []
    for p in posts[:15]:
        try:
            html = c.get(p).text
            bad = PHANTOM.findall(html)
            if bad:
                found.append((p, sorted(set(bad))[:4]))
        except Exception:  # noqa: BLE001
            continue

    print("posts with phantom-style tag links:")
    for p, b in found:
        print(f"  {p}")
        for x in b:
            print(f"      {x}")
    if not found:
        print("  (none in first 15 posts)")


if __name__ == "__main__":
    main()