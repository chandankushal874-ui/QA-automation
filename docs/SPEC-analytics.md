# Spec: Cross-Job Analytics

## Objective

Turn per-job data into **trends over time** so teams see quality direction, not
snapshots: pass-rate per day/week, issue severity/category breakdowns, flaky
rate trend, per-target rollups, and regression/recovery velocity. Today
`GET /api/dashboard/stats` returns only lifetime totals; buyers need "is my
site getting better or worse?"

## Non-goals (v1)

- No per-user/org segmentation beyond existing owner scoping.
- No export/CSV (data is available via API; UI-first).
- No retention rollup tables (v1 aggregates live; volumes here are small —
  thousands of jobs, tens of thousands of issues).

## API surface (additive, owner-scoped like /api/dashboard/stats)

```
GET /api/analytics/trends?days=30        → time series
GET /api/analytics/issues?days=30        → severity + category breakdowns (all-time-windowed)
GET /api/analytics/targets?days=30       → per-target (job.url) rollup: crawls, issues, pass rate
GET /api/analytics/flaky?days=30         → flaky registry stats trend (samples, quarantine rate)
```

All accept `days` (1-365, default 30). Admins see global data; users see own.

### trends payload

```json
{
  "days": 30,
  "series": [
    {"date": "2026-10-01", "crawls": 4, "completed": 3, "failed": 1,
     "issues": 12, "blocking": 1, "functional": 4, "visual": 2, "advisory": 5,
     "pages_visited": 210}
  ],
  "totals": {"crawls": 38, "issues": 130, "pass_rate": 0.82}
}
```

`pass_rate` = completed / (completed + failed) over the window.

### issues payload

```json
{
  "days": 30,
  "by_severity": {"blocking": 3, "functional": 12, "visual": 8, "advisory": 40},
  "by_category": {"js_error": 10, "network_failure": 6, "security": 4},
  "top_recurring": [
    {"fingerprint": "abc", "title": "JS_ERROR: x is undefined",
     "occurrences": 5, "severity": "blocking", "last_seen": "2026-10-18"}
  ]
}
```

### targets payload

Grouped by `Job.url`; rollup = crawls, distinct issue fingerprints, blocking
count, avg issues per crawl, last crawl status.

### flaky payload

Per-day: new fingerprints observed, quarantined count, samples in registry,
plus current top flaky fingerprints by flip rate.

## Implementation

- `src/api/analytics.py` (new router, mounted additively) — pure SQLAlchemy
  aggregation, no new tables, no migrations. SQLite + PG compatible date
  grouping via `func.date(...)` (both support it).
- `GET /api/analytics/*` reuse `get_current_user` + owner filter (admin = all).

## Files

```
src/api/analytics.py                 NEW (router + aggregations)
src/api/app.py                        EXTEND (mount router)
tests/test_analytics.py               NEW (seed jobs/issues → assert series math)
frontend/src/lib/api.ts                EXTEND (4 client fns)
frontend/.../_layout.analytics.tsx    NEW (Analytics tab: charts + tables)
frontend nav + routeTree               EXTEND
```

## Success criteria

1. Seeded jobs/issues produce correct per-day series (dates bucketed by local
   day; zero-fill missing days).
2. Owner isolation: User A's analytics never include User B's jobs.
3. `days` bounds validated (400 outside 1..365).
4. Trends/issues/targets/flaky all return the documented shapes.
5. Analytics tab renders: issue trend, severity mix, top targets, top
   recurring issues; frontend build green.
6. Full suite + ruff green.