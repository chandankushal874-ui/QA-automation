# Spec: Multi-Step Journeys (declarative)

## Objective

Let a QA engineer define a **multi-step user flow** — login → browse → act → assert — and run it across the browser matrix, capturing per-step issues. Today the platform *crawls* and has auth bypass (login happens automatically), but there is no way to author and replay a **specific** flow (checkout, settings change, delete-then-verify). The `Journey` model already exists in the schema but is dead code (no executor, no API, no UI, no tests).

## Non-goals (v1)

- No Playwright *code* authoring (devs can use ci_gate + their own tests). We ship **declarative step lists**.
- No recording/replay from a browser session (future).
- No cross-step data binding/`$variables` (future) — v1 steps are literal strings.

## Data model (existing `Journey`, extended)

`Journey` already has: `id, job_id, name, description, status, priority, is_critical_path, steps (JSON), results (JSON), console_errors (JSON), created_at, started_at, completed_at, duration_seconds`.

`steps` becomes a list of typed step dicts (see below). `results` becomes
`{ "<browser>": { passed, step_results: [...] } }`. **Owner scoping** is inherited
via `job_id` → `Job.owner_id` (a journey belongs to a job); we also add an
`owner_id` column for direct owner lookup, mirroring other tenant-isolated tables.

### Step schema (v1)

```json
{ "action": "goto",         "url": "https://..." }
{ "action": "fill",         "selector": "#email", "value": "user@example.com" }
{ "action": "click",        "selector": "button[type=submit]" }
{ "action": "wait",         "ms": 1500 }                      // or "selector": ".loaded"
{ "action": "assert",       "selector": ".dashboard", "expect": "visible" }  // visible|hidden|text
{ "action": "screenshot",   "label": "checkout-complete" }    // capture + attach
{ "action": "login",        "username_selector": "#username", "password_selector": "#password",
                            "submit_selector": "form button", "username": "...", "password": "..." }
```

`assert` with `expect: "text"` also takes `"text": "expected substring"`.

## Execution semantics

- A journey runs **after** the crawl of its job completes (or standalone via
  `POST /journeys/{id}/run`), inside the same worker, reusing the browser factory.
- Runs once per enabled browser in the job matrix (or a journey-specified subset).
- Each step executes against a **fresh instrumented page**; `goto` reuses the
  existing `InstrumentedPage` console-capture so JS errors/network failures on
  any step are captured and triaged as issues.
- `login` step reuses the existing `AuthBypassEngine`/`FormLoginStrategy` so
  session state persists across subsequent steps (same browser context).
- A step failure is **non-fatal by default**: record it, continue to next step
  (configurable `stop_on_failure: true`).
- Every step result carries `status` (`passed`/`failed`/`error`/`skipped`),
  `error_message`, and optional `screenshot_key`.

## API surface

```
POST   /api/journeys                          create (owner-scoped; body: name, steps, browser_matrix, job_id?)
GET    /api/journeys                          list own journeys
GET    /api/journeys/{id}                     detail (steps + results)
PATCH  /api/journeys/{id}                     edit steps/name
DELETE /api/journeys/{id}                     delete
POST   /api/journeys/{id}/run                 enqueue execution (returns run id/status)
GET    /api/journeys/{id}/run/{run_id}        run status + per-step results
```

All routes require auth + owner isolation (404 anti-enumeration, admin override), identical to `projects.py`.

## Commands

```
Test: python -X utf8 -m pytest tests/ -q
Lint: python -m ruff check src tests scripts
Dev:  python -m src.main
```

## Files

```
src/api/journeys.py               NEW (router, mirroring projects.py)
src/analysis/journey_runner.py    NEW (step executor using browser factory)
src/models/database.py            EXTEND (Journey.owner_id; no new tables)
src/crawler/engine.py             EXTEND (invoke runner for journeys attached to a job — additive)
tests/test_journeys_api.py        NEW
tests/test_journey_runner.py      NEW
frontend/.../_layout.journeys.tsx NEW (list + author)
frontend/.../_layout.journey.$id.tsx NEW (detail + run + results)
frontend/src/lib/api.ts           EXTEND
```

## Success criteria

1. `POST /api/journeys` + `POST /api/journeys/{id}/run` runs a `goto→login→goto→assert` flow against the fixture app and returns per-step `passed` results.
2. An `assert` that fails produces a `failed` step with a clear `error_message` and is reflected in the journey result; `stop_on_failure` honored.
3. Owner isolation: User A gets 404 on User B's journey.
4. A journey reuses auth state: after a `login` step, a `goto /dashboard` `assert` passes.
5. Runner is non-fatal: a failed step does not crash the worker.
6. Frontend list/detail/run pages render and can run a journey.
7. Full suite + ruff green.
