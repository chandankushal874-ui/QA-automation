# False-Positive Study: Platform Report vs Independent Audit — ollavpn.com

**Date:** 2026-10-20 · **Platform build:** `f6e3879` (deployed docker stack) · **Crawl:** chromium, 25 pages, depth 2, ~9 min · **Discovery:** 513 endpoints

## Method

1. Platform crawled `https://ollavpn.com` through its normal pipeline (Playwright worker → issue triage).
2. I independently audited the same site with plain HTTP (httpx + browser UA): all homepage links, static assets, titles/descriptions, security headers, mixed content, sitemap/robots.
3. I then re-verified **every platform finding one by one** against the live site (re-requesting the exact claimed URLs, checking sitemap membership, tracing discovery source in HTML, and identifying frame origins for console errors).

## Platform findings (15) — verdict on each

| # | Finding | Severity | Verdict |
|---|---------|----------|---------|
| 1 | `Sitemap URL returns 404 — /tags/streaming/` | functional | **TRUE POSITIVE** — sitemap declares it; 404 confirmed on 3 repeat hits |
| 2 | `Sitemap URL returns 404 — /author/vikas-swami/` | functional | **TRUE POSITIVE** — sitemap declares it; 404 confirmed |
| 3 | `Sitemap URL returns 404 — /tags/Security/` | functional | **PARTIAL** — real 404, but URL is a broken blog-post href, *not* sitemap-declared; label wrong |
| 4 | `Sitemap URL returns 404 — /tags/WireGuard/` | functional | **PARTIAL** — same as #3 |
| 5 | `Sitemap URL returns 404 — /tags/Privacy/` | functional | **PARTIAL** — same as #3 |
| 6 | `Sitemap URL returns 404 — /tags/Security%20&amp;%20Banking/` | functional | **PARTIAL + CORRUPTED** — real broken href `/tags/Security &#38; Banking/` on a blog post; platform failed to unescape the entity, reported a garbage double-encoded URL |
| 7 | `Sitemap URL returns 404 — /tags/Privacy%20&amp;%20Security/` | functional | **PARTIAL + CORRUPTED** — same as #6 |
| 8 | `/status exposed (HTTP 200)` | advisory | **FALSE POSITIVE** — intentional public status page |
| 9 | `/robots.txt exposed (HTTP 200)` | advisory | **FALSE POSITIVE** — robots.txt literally says "We WANT to be crawled" |
| 10 | `/sitemap.xml exposed (HTTP 200)` | advisory | **FALSE POSITIVE** — intentional public file |
| 11 | `/.well-known/security.txt exposed (HTTP 200)` | advisory | **FALSE POSITIVE** — intentional (that's its entire purpose) |
| 12 | `/humans.txt exposed (HTTP 200)` | advisory | **FALSE POSITIVE** — intentional |
| 13 | `/favicon.ico exposed (HTTP 200)` | advisory | **FALSE POSITIVE** — intentional |
| 14 | `/llms.txt exposed (HTTP 200)` | advisory | **FALSE POSITIVE** — intentional (AEO file for AI crawlers) |
| 15 | `CONSOLE_ERROR: No available adapters.` | advisory | **FALSE POSITIVE** — from the Cloudflare Turnstile iframe (`challenges.cloudflare.com`) on `login.ollavpn.com`; third-party origin, benign headless-environment log; main site never loads that script |

## My independent audit (comparison baseline)

- 34 homepage-linked pages: **all 200**, 0 broken
- 0 broken static assets (js/css/img/fonts), 0 mixed content
- All key pages have title + meta description
- Security headers: **excellent** — HSTS with preload, full CSP, `X-Frame-Options: DENY`, `nosniff`, `referrer-policy` (platform does not surface positive security posture)
- Content inconsistency found by human review (outside platform's remit): homepage says "Free forever. No paid tiers. No catch." while `/pricing` says "Pro from $2.99/mo"
- robots.txt explicitly invites crawling/search engines
- CDN 403s plain non-browser HTTP clients (the platform's real-browser crawl passed this wall cleanly — a strength)

## Scorecard

| Dimension | Result |
|---|---|
| Strict precision | 2/15 clean true positives; 5/15 real-signal-but-flawed; **8/15 false positives (53%)** |
| Severity-weighted precision | blocking: **0 findings, 0 FPs** — gate decision (PASS) was correct; functional: 7/7 pointed at real defects (2 clean, 5 misattributed/corrupted); advisory: 7/7 noise |
| Recall | Platform **found 7 real site defects my naive homepage audit missed** (broken tag hrefs on blog posts, dead sitemap URLs). Genuinely valuable. |
| Noise concentration | All pure noise sits in the advisory tier — the gate tier stayed clean |
| Flaky registry | All findings `observed` on first run — correct cold-start behavior |

**Bottom line:** at *gate* level the platform performed well (no blocking false positives, correct PASS). At *report* level, 53% of findings were noise — but concentrated in advisory severity, and every functional finding pointed at a real defect. The platform's standout win: it caught broken internal links and dead sitemap URLs deep in the site that a homepage-only manual pass misses.

## Platform root causes identified by this study

1. **Security probe has no allowlist for intentionally-public files** (robots.txt, sitemap.xml, favicon.ico, humans.txt, security.txt, llms.txt, /status) → 7 of 8 FPs.
2. **HTML entities are not unescaped in mined hrefs** (`&amp;`/`&#38;`) → corrupted URLs reported verbatim as findings (#6, #7).
3. **Discovery-source mislabeling**: the "Sitemap URL returns 404" label is applied to URLs that are *not* in the sitemap (they came from blog-post hrefs).
4. **Console attribution ignores frame origin**: a third-party Turnstile iframe's internal log is reported as a site console error.

## Recommended fixes (prioritized)

- **P0** — `html.unescape()` mined hrefs; refuse to emit finding URLs containing literal `&amp;`/`&#38;`.
- **P0** — Security probe: allowlist well-known public files; downgrade "exposed" 200s on them to info or skip.
- **P1** — Only use the sitemap-404 label when the URL is literally sitemap-declared; otherwise label "Internal link returns 404".
- **P1** — Console-error frame-origin attribution; ignore known third-party challenge/CDN origins (challenges.cloudflare.com etc.).
- **P2** — Surface positive security-posture checks (HSTS/CSP/XFO) to balance the "exposed" noise.

## Reproduction artifacts

- `scripts/manual_audit_ollavpn.py` — my independent audit
- `scripts/verify_findings.py` — per-finding adjudication
- `scripts/trace_phantom_urls.py`, `scripts/find_phantom_posts.py` — phantom-URL root-cause tracing
- `scripts/poll_fpstudy.py`, `scripts/fetch_fpstudy_report.py` — crawl harness
## Post-fix validation (same crawl, same site, fixes deployed)

Re-ran the identical crawl after implementing P0+P1:

| Run | Findings | True positives | False positives | Precision |
|---|---|---|---|---|
| Before fixes | 15 | 2 clean (+5 flawed-but-real) | 8 | 47% |
| After fixes | 5 | 5 | 0 | 100% |

The 5 remaining findings are all sitemap-declared URLs that genuinely return
404 (`/tags/Security/`, `/tags/Privacy/`, `/tags/WireGuard/`, `/tags/streaming/`,
`/author/vikas-swami/` - declared in the hidden `sitemap-0.xml`, reachable only
via sitemap-index recursion). Worker log: `264/269 OK, 5 missing`.

### Correction to the original study (root-cause depth)

The original study mislabeled findings #3-#7 as "PARTIAL - not in sitemap".
Deeper tracing found a fifth sitemap (`sitemap-0.xml`) that the naive manual
audit missed - it is reachable only via `<sitemap><loc>` recursion from
`sitemap-index.xml`. Those capitalized/entity tag URLs ARE genuinely
sitemap-declared and DO 404 - true positives all along, emitted with a
correct label. The entity-corruption finding stands (the two `&amp;`-encoded
URLs return 200 once unescaped, so pre-fix they were falsely reported as
404s). Revised original run: 7 true signals (2 corrupted by the entity bug),
8 false positives. The platform's sitemap-index recursion (finding URLs my
manual audit missed entirely) is a genuine recall advantage.

### Fixes implemented and verified in the deployed stack

1. P0 - Security probe BENIGN allowlist (security_probe.py): robots.txt,
   sitemap.xml, favicon.ico, humans.txt, security.txt, llms.txt, ads.txt,
   LICENSE, /status get a BENIGN verdict - probed for visibility, never an
   Issue. (-7 FPs)
2. P0 - HTML entity unescaping: endpoint_registry.add() unescapes any URL
   containing `&`; sitemap `<loc>` extraction unescapes at parse time. The two
   `&amp;` URLs now resolve to their real 200 pages. (-2 corrupted findings)
3. P1 - Console third-party frame-origin gate (engine.py): console entries
   whose source_location points at a known third-party widget origin
   (Turnstile, reCAPTCHA, insights, social embeds) are skipped; plus a message
   gate for network-failure entries embedding third-party URLs. (-1 FP, plus
   the Turnstile fetch failure seen in the second run)
4. P1 - First-party subdomain awareness: same registered domain
   (login.ollavpn.com <-> ollavpn.com) is never filtered - verified by tests.

Tests: tests/test_false_positive_fixes.py (18 tests covering all gates); full
suite 362 passed. Deployed stack rebuilt; validation crawl: 5/5 true
positives, 0 false positives, console summary all-zero.
