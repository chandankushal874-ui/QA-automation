"""Find which sitemap (robots-declared) contains the capitalized tag URLs."""

from __future__ import annotations

import re

import httpx


def main() -> None:
    c = httpx.Client(headers={"User-Agent": "Mozilla/5.0"}, timeout=20, follow_redirects=True)
    robots = c.get("https://ollavpn.com/robots.txt").text
    sitemaps = re.findall(r"Sitemap:\s*(\S+)", robots, re.I)
    print("declared sitemaps:", sitemaps)

    targets = [
        "/tags/Security/",
        "/tags/WireGuard/",
        "/tags/Privacy/",
        "/tags/streaming/",
        "/author/vikas-swami/",
    ]
    for s in sitemaps:
        try:
            t = c.get(s).text
        except Exception as e:  # noqa: BLE001
            print(f"  {s} ERR {e}")
            continue
        locs = re.findall(r"<loc>(.*?)</loc>", t, re.I)
        print(f"  {s}: {len(locs)} locs")
        for target in targets:
            exact = [l for l in locs if l.strip() == f"https://ollavpn.com{target}"]
            loose = [l for l in locs if target.rstrip('/').split('/')[-1] in l]
            if exact or loose:
                print(f"      {target}: exact={exact} loose={loose[:3]}")


if __name__ == "__main__":
    main()