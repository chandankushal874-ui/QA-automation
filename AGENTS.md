# AGENTS.md — Cross-Browser Testing Platform

> **For any AI agent (Copilot, Cursor, Continue, etc.) working on this project.**
> Read this first. It explains the architecture, conventions, and what to do.

---

## What This Project Is

An **autonomous cross-browser web testing platform**. You paste a URL, it:
1. Crawls the site across Chromium/Firefox/WebKit
2. Discovers all endpoints (SPA states, API routes, GraphQL, OpenAPI)
3. Detects bugs (JS errors, network failures, visual regressions, a11y issues)
4. Suggests AI-powered fixes via OpenRouter LLM
5. Verifies fixes in remote sandboxes (Daytona → E2B → local fallback)
6. Generates HTML dashboards with glassmorphic UI

**Stack:** Python 3.12+, FastAPI, Playwright, SQLAlchemy (Postgres + SQLite fallback), Redis/RQ, OpenRouter, E2B, Daytona

---

## Project Structure

```
cross-browser-testing-platform/
├── src/
│   ├── main.py                  # Entry: uvicorn runner
│   ├── config.py                # pydantic-settings, loads .env from project root
│   ├── api/
│   │   └── app.py               # FastAPI orchestrator — all REST endpoints
│   ├── crawler/
│   │   └── engine.py            # CrawlEngine — the core crawl loop (Phase 3)
│   ├── analysis/
│   │   ├── fix_engine.py        # StaticAnalyzer + OpenRouterFixer + VerificationEngine
│   │   ├── visual_regression.py # Screenshot diffing
│   │   ├── accessibility.py     # axe-core a11y audits
│   │   ├── api_tester.py        # API endpoint testing
│   │   ├── allure_reporter.py   # Allure report generation
│   │   └── report_generator.py  # HTML dashboard generation
│   ├── browser/
│   │   └── factory.py           # Playwright BrowserContextFactory + InstrumentedPage
│   ├── auth/
│   │   └── bypass.py            # Pluggable auth bypass (10 strategies)
│   ├── discovery/
│   │   ├── mining.py            # DOM/HTML mining, robots.txt, sitemap.xml
│   │   ├── js_graphql.py        # JS bundle scanning + GraphQL introspection
│   │   ├── spa_graph.py         # SPA state-flow graph (BFS routing)
│   │   ├── openapi.py           # OpenAPI/Swagger auto-discovery
│   │   ├── prioritization.py    # Priority scoring for crawl queue
│   │   └── security_probe.py    # Sensitive-path probing + SPA false-positive elimination
│   ├── core/
│   │   ├── console_capture.py   # Console message classification
│   │   ├── dom_similarity.py    # DOM structural comparison
│   │   ├── endpoint_registry.py # Central endpoint store with dedup
│   │   ├── frontier.py          # Redis-backed priority crawl frontier
│   │   ├── mock_redis.py        # In-memory Redis mock for testing
│   │   └── scrubber.py          # PII/text scrubbing for LLM prompts
│   ├── models/
│   │   └── database.py          # SQLAlchemy models (Job, Issue, CrawlState, etc.)
│   └── workers/
│       └── worker.py            # RQ worker entry point
├── tests/                       # 151 unit + integration tests (all green)
├── fixture_app/                 # Deliberately buggy test target app
├── frontend/                    # React/Vite frontend
├── scripts/
│   └── honest_deep_test.py      # Deep integration test script
├── alembic/                     # DB migrations
├── baselines/                   # Visual regression baselines
├── reports/                     # Generated HTML reports
├── docker-compose.yml           # Postgres + Redis + MinIO + API + Worker
├── Dockerfile                   # Playwright + browsers pre-installed
├── pyproject.toml               # Dependencies & tool config
├── .env                         # API keys (E2B, Daytona, OpenRouter)
└── .env.example                 # Template without secrets
```

---

## Key Architecture Decisions

### 1. Config Loading
`src/config.py` uses **pydantic-settings** with an **absolute path** to `.env`:
```python
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
```
This means the server can be launched from any CWD and still find keys. If you add new env vars, add them to BOTH `config.py` (Settings class) AND `.env.example`.

### 2. Database: Postgres with SQLite Fallback
`src/api/app.py` tries Postgres first, falls back to SQLite (`cbtp.db`). The SQLAlchemy models use `JSON_TYPE` that maps to JSONB on Postgres and JSON on SQLite. **Always test with both.**

### 3. Sandbox Verification Chain (Step W)
`VerificationEngine.verify_patch()` in `src/analysis/fix_engine.py`:
```
Daytona (preferred, sub-90ms cold start)
  → E2B (fallback)
    → local py_compile (last resort)
```
- Daytona: stateful sandbox, `daytona-small` snapshot, `pip install -e .` + `pytest`
- E2B: zip upload, `pip install -e .`, overwrite target file, `pytest`
- Local: `py_compile` syntax check only

### 4. Crawl Engine Resilience
`CrawlEngine._ensure_healthy_page()` in `engine.py` — if a Playwright page crashes mid-crawl, it auto-recreates the page. This fixed the "38/52 pages" stall bug.

### 5. Destructive Action Gating (Step P)
`is_destructive_action()` blocks DELETE methods, paths with `/delete|/destroy|/remove|/purge|/format|/wipe|/clear|/reset|/drop`, and button labels with destructive words. Exceptions: "cookie" and "filter" are safe.

### 6. OpenRouter Integration
- Endpoint: `{base_url}/chat/completions` (NOT just base_url)
- Default model: `openai/gpt-oss-20b:free`
- Responses may wrap JSON in markdown — use regex `\{[\s\S]*\}` to extract
- Always set `max_tokens: 2048`
- Falls back to mock fixes if no API key

### 7. Windows-Specific
- `run_background_crawl_job()` uses `ProactorEventLoop` on Windows (required for subprocess)
- PowerShell mangles inline Python — write scripts to files instead

### 8. Security Probe Verdicts & Report Intelligence
`src/discovery/security_probe.py` classifies every probed path with a **verdict**:
`CONFIRMED_EXPOSED` / `FALSE_POSITIVE` / `SPA_FALLBACK` / `NOT_FOUND` / `PROTECTED` / `SERVER_ERROR` / `NETWORK_ERROR`.
- Verdicts are **framework-agnostic**: they depend only on `(http_status, content_type, body signature, SPA-shell fingerprint)` — never on the framework name. Framework labels (Angular/React/Vue/Next/generic) are display-only.
- SPA/catch-all detection: `detect_spa()` requests a guaranteed-bogus path; the response body becomes the soft-404 shell fingerprint (`_is_spa_shell_body`, fuzzy ≥0.90).
- Only `CONFIRMED_EXPOSED` (and 5xx) create `Issue` rows. False positives NEVER create issues.
- Probe categories: `categorize_probe()` → `Security/File` (body-signature validated), `Security/Route` (HTML is valid), `Security/Static` (content-type validated).
- Console `SPA_ROUTE_ERROR` entries ("Cannot match any routes" etc.) do NOT create issues — they are recorded to `job.config["spa_route_404s"]`. Bare `STACKTRACE:` console fragments are dropped as noise.
- Health score (`report_generator.compute_health_score`): only *tested* endpoints (`last_status_code` set) count — discovered-but-unvisited URLs are excluded from the denominator. Broken = real HTTP ≥400 **plus broken internal links**: organically-linked endpoints (source `dom_link`/`sitemap_xml`/`redirect`/`header_link`/`html_comment`/`hidden_input`, or an `also_found_via_*` extra) with a SPA route error (`effective_status_code`). Probe/wordlist-only SPA fallbacks (e.g. `api_brute_force`) stay exempt — see `_is_organically_linked` / `_is_broken_internal_link` / `_is_spa_fallback_only`. Broken links render a red `BROKEN LINK` badge and a QA recommendation; probe-only fallbacks keep the informational SPA Routing bucket.
- Probe run data is persisted in `job.config` (no schema migration): `security_probe_results` (per-path verdicts), `spa_type`, `spa_route_404s`. Old jobs without these keys still render (new report sections degrade to "N/A" or are omitted).
- HTML report (`report_generator.py`) includes: 7-card summary stats grid, Key Findings, Security Probe Results table (verdict/severity badges), Results by Category, and verdict badges in place of the old "Broken (SPA 404)" label. When mutating `job.config`, reassign the whole dict (`job.config = {...}`) so SQLAlchemy change tracking persists it.

### 9. Job Dispatch: RQ Workers with Thread Fallback
`POST /jobs` (`src/api/app.py::create_job`) enqueues `src.api.app.run_background_crawl_job` to the RQ `default` queue when Redis is reachable; otherwise it falls back to an in-process daemon thread (local dev unchanged). In Docker, `worker` service replicas (`WORKER_REPLICAS` in `.env.production`, default 20) each run one crawl at a time — concurrency = replica count (~1–1.5 GB RAM per worker). Jobs beyond capacity wait in the Redis queue; nothing is dropped. Watch via `GET /metrics` (`queue_depth`, `active_workers`) or the RQ dashboard.

### 10. Docker One-Command Deployment
`start.bat` / `start.sh` wraps `docker compose -f docker-compose.prod.yml --env-file .env.production up -d`. The Python image (api/worker/fixture) installs deps via `pip install .` + `psycopg2-binary` (sync PG driver, intentionally not in pyproject.toml) + `playwright install --with-deps chromium firefox webkit`. The frontend image is a multi-stage Node build → nginx static serve; `frontend/.dockerignore` excludes host `node_modules`/`dist`/`.env.local` so the prod build gets `VITE_API_URL=""` (same-origin via the nginx proxy). `Base.metadata.create_all` races between gunicorn workers are tolerated by design (guard in `app.py`); the DB engine forces a test connection at startup so a dead Postgres still triggers the SQLite fallback.

---

## How to Run

```bash
# 1. Start infrastructure
docker compose up -d postgres redis minio

# 2. Start the API server
python -m src.main
# or: uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload

# 3. Start the fixture app (for local testing)
cd fixture_app && python app.py

# 4. Run tests
pytest tests/ -q

# 5. Run the deep integration test
python scripts/honest_deep_test.py
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/jobs` | Submit a crawl job (URL + config) |
| GET | `/jobs/{id}` | Job status + live progress |
| GET | `/jobs/{id}/report` | Full JSON report with issues |
| GET | `/jobs/{id}/dashboard` | Interactive HTML dashboard |
| GET | `/jobs` | List all jobs |
| GET | `/health` | Health check |

---

## Current State (as of 2026-07-20)

- **All 151 tests pass** (Steps A through Z complete)
- `.env` has live keys for E2B, Daytona, and OpenRouter
- Daytona SDK is installed and preferred for sandbox verification
- The project is **production-ready** — all phases implemented and verified

---

## Common Tasks for Agents

### Adding a new API endpoint
1. Add Pydantic models in `src/api/app.py`
2. Add the route handler
3. Add tests in `tests/test_api.py`

### Adding a new discovery method
1. Create module in `src/discovery/`
2. Register discovered endpoints via `EndpointRegistry`
3. Wire into `CrawlEngine` in `src/crawler/engine.py`
4. Add tests in `tests/test_discovery_*.py`

### Adding a new analysis/issue type
1. Add category to `IssueCategory` enum in `src/models/database.py`
2. Create analyzer in `src/analysis/`
3. Wire into `CrawlEngine` crawl loop
4. Add tests

### Modifying the fix engine
- `OpenRouterFixer` handles LLM calls
- `VerificationEngine` handles sandbox verification
- `StaticAnalyzer` extracts source context from repos
- The chain is: StaticAnalyzer → OpenRouterFixer → VerificationEngine

### Working with sandboxes
- Daytona is preferred (faster, stateful)
- E2B is fallback
- Both require API keys in `.env`
- Both zip the repo, upload, `pip install -e .`, apply patch, run pytest
- If no repo_dir provided, falls back to syntax-only check

---

## Important Conventions

- **Python 3.12+** only (uses `StrEnum`, new typing syntax)
- **Line length:** 120 (ruff config)
- **Imports:** `from __future__ import annotations` everywhere
- **Async:** All I/O is async (Playwright, HTTP, DB)
- **Logging:** Use `logger = logging.getLogger(__name__)`, not print()
- **Error handling:** Never let a crawl stall — catch, log, recover
- **Security:** Scrub all user data before sending to LLMs (`scrub_text()`)
- **Testing:** SQLite in-memory for unit tests, real Postgres for integration
