# Spec: Visual Baseline Regression + CI-First Diff Gating

## Objective

Turn the platform from a *single-crawl detector* into a *regression engine* that the market buys for two concrete workflows:

1. **Visual baseline & diffing (#1)** — a user establishes an approved baseline for a `Project` (per branch), then every subsequent crawl of that project diffs against the baseline and surfaces a reviewable, side-by-side/overlay diff with ignore regions for dynamic content.
2. **CI-first PR gating (#2)** — a one-line SDK submits a crawl bound to a project+branch, compares against the baseline, fails the PR check on regressions above threshold, and posts an inline PR comment with diff images and a review link.

### Users & success

- **QA engineer** creates a Project, runs an initial crawl → "set as baseline", edits ignore regions, then trusts that the next crawl reports *only real visual changes*.
- **Developer** opens a PR; the CI check compares the preview deploy against `main`'s baseline and blocks the PR when a real visual regression appears, with a comment linking to the diff.

## Capability Map

| Module id | Responsibility | Depends on |
|---|---|---|
| `project-model` | `Project` entity + CRUD + auth (owner-scoped) | — |
| `s3-storage` | boto3/MinIO client: put/get objects, signed URLs, bucket bootstrap | — |
| `baseline-engine` | Baseline capture, pixel-diff, ignore regions, match/change/regression verdicts | project-model, s3-storage |
| `diff-api` | REST endpoints: project/baseline/diff CRUD, diff-viewer payloads, approve/ignore | baseline-engine |
| `ci-sdk` | `ci_gate.py` baseline/diff awareness + PR inline comment (images + review link) | diff-api |
| `diff-ui` | Frontend: project mgmt, baseline approval, diff viewer, ignore-region editor | diff-api |

Build order: `project-model` → `s3-storage` → `baseline-engine` → `diff-api` → `ci-sdk` → `diff-ui`.

## Tech Stack

- Python 3.12+, FastAPI, SQLAlchemy 2 (asyncpg + SQLite fallback), **boto3 1.40.76** (MinIO S3), Pillow (pixel diff), httpx, pytest.
- Frontend: React 19 + TanStack Router + Tailwind (existing).

## Commands

```
Test:      python -X utf8 -m pytest tests/ -q
Lint:      python -m ruff check src tests scripts
Fix:       python -m ruff check --fix src tests scripts
Typecheck: python -m mypy src  (if configured; otherwise ruff + tests gate)
Dev:       python -m src.main
Docker:    docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

## Project Structure

```
src/
  core/
    s3.py                 # NEW: thin boto3/MinIO client (bucket bootstrap, put/get, presigned URL)
  analysis/
    baseline.py           # NEW: baseline engine — pixel diff + ignore regions + verdicts
    visual_regression.py  # KEPT unchanged (legacy, non-project jobs)
  api/
    projects.py           # NEW: Project + baseline/diff REST endpoints (mounted into app.py)
  models/
    database.py           # EXTEND: Project + VisualBaseline models
scripts/
  ci_gate.py              # EXTEND: --project/--branch/--baseline + diff-gate + PR comment
tests/
  test_s3.py              # NEW
  test_baseline_engine.py # NEW
  test_projects_api.py    # NEW
  test_ci_gate_diff.py    # NEW
frontend/src/
  routes/_app/_auth/dashboard/_layout.projects.tsx      # NEW
  routes/_app/_auth/dashboard/_layout.project.$id.tsx    # NEW: baseline + diff viewer
  lib/api.ts              # EXTEND: project/diff client fns
```

## Code Style

- `from __future__ import annotations`; `logger = logging.getLogger(__name__)` (never print).
- Python 3.12 `StrEnum`, `str | None` typing; line length 120.
- Async I/O (boto3 is sync → wrap in `run_in_threadpool` or `asyncio.to_thread`).
- Pydantic request/response models; SQLAlchemy `Mapped`/`mapped_column`.
- Tenant isolation: every project/baseline row is owner-scoped; S3 object keys are namespaced `tenants/{ns}/projects/{pid}/baselines/{branch}/{key}`.

## Data Model (additions)

```python
class Project(Base):
    __tablename__ = "projects"
    id, owner_id (FK platform_users, CASCADE), name, slug (unique per owner),
    base_url, default_branch ("main"), viewports (JSON, default [{"width":1280,"height":720}]),
    ignore_regions (JSON, default []), is_active, created_at, updated_at
    # unique: (owner_id, slug)

class VisualBaseline(Base):
    __tablename__ = "visual_baselines"
    id, project_id (FK CASCADE), branch, url, browser, viewport (JSON {w,h}),
    object_key (S3), thumb_key (S3), image_hash (SHA-256), width, height,
    status ("active"|"superseded"), created_at
    # unique: (project_id, branch, url, browser, viewport) — one active baseline per identity
```

Baseline identity = `(project, branch, url, browser, viewport)`. `diff` of a new screenshot computes mismatch vs the *active* baseline for that identity; a `regression` is any change exceeding threshold (configurable per project) that is not covered by an ignore region.

## Diff & Ignore Regions

- **Pixel diff** via Pillow `ImageChops.difference` (reuse tolerance semantics), produces: `mismatch_pct`, `changed_pixels`, `diff_image` (red overlay PNG), `regions` list.
- **Ignore regions**: list of `{selector}` and/or `{x,y,width,height}` rectangles; engine masks those pixels before counting mismatch. Regions are defined per project (and optionally per-URL), edited in the UI.
- **Verdict**: `new_baseline` (no baseline existed), `match`, `change`, `regression` (change > threshold), `error`.
- Diff artifacts (baseline, current, diff overlay, thumbs) are stored in S3 and served via signed URLs (or the existing `/screenshots` passthrough for compatibility).

## API Surface

```
POST   /api/projects                       create project
GET    /api/projects                       list (owner-scoped)
GET    /api/projects/{id}                  detail
PATCH  /api/projects/{id}                  update (ignore_regions, viewports, threshold)
DELETE /api/projects/{id}                  delete
POST   /api/projects/{id}/baseline         set/capture baseline (from a job's screenshots, or upload)
GET    /api/projects/{id}/baselines        list active baselines
POST   /api/projects/{id}/compare          diff a job's screenshots vs baseline (returns per-URL verdicts)
GET    /api/projects/{id}/compare/{cid}    diff result payload (images + regions)
POST   /api/projects/{id}/compare/{cid}/approve   promote current → new baseline
POST   /api/projects/{id}/compare/{cid}/ignore    mark changed URLs ignored (no-op for gate)
GET    /api/projects/{id}/diff/{url_key}/{browser}   diff image + side-by-side payload
```

All routes require auth; project routes enforce `owner_id == current_user.id` (admin override allowed).

## CI SDK

`ci_gate.py` gains:
- `--project <slug>` `--branch <branch>` `--baseline-branch main` `--max-visual <n>` `--fail-on-regression`
- After crawl: if project+branch set, call `POST /api/projects/{id}/compare`, fold visual `regression` count into the gate (quarantined flaky + ignored regions excluded).
- Print a GitHub step summary + emit `::error::` annotations per regression; emit a JSON machine-readable block for the comment step.
- Workflow `qa-crawl-gate.yml` + composite action updated to pass project/branch and post an inline comment with diff image URLs + review link.

## Testing Strategy

- Unit: baseline engine (pixel diff math, ignore-region masking, verdicts) with synthetic PIL images — no network.
- Unit: S3 storage layer against a fake S3 client (moto or a stub) — no live MinIO in CI.
- Integration: project CRUD + auth isolation (User A cannot read User B's project) on SQLite.
- CI gate: diff-gate logic with a mocked API client.
- Existing 289 tests must stay green.

## Boundaries

- **Always:** owner-scope every row/object; run full `pytest` + `ruff check` before commit; keep legacy `visual_regression.py` behavior intact for non-project jobs.
- **Ask first:** schema migrations via Alembic (use `Base.metadata.create_all` additive columns only), adding new deps beyond boto3/Pillow, changing the compose/nginx config.
- **Never:** commit S3/MinIO or DB credentials; weaken tenant isolation; remove existing tests.

## Success Criteria

1. `Project` CRUD + auth isolation covered by tests; User A gets 403 on User B's project.
2. Baselines persist in MinIO under tenant namespaced keys and survive API restarts.
3. A crawl of a project with an active baseline produces per-URL `regression` verdicts honoring ignore regions; a `--max-visual` CI gate fails correctly on regression and passes on match.
4. `ci_gate.py --project/--branch` runs end-to-end against the fixture app (local), and the PR comment contains diff image URLs + review link.
5. Diff viewer renders side-by-side/overlay with ignore-region editing.
6. Full test suite green; `ruff check` clean.

## Open Questions

- Alembic: is there an active migrations directory, or should additive columns ride on `create_all` (matches existing pattern — no `alembic/` dir seen)? **Assumption: additive via `create_all`, matching current project convention.**
- Signed-URL expiry: default 15 min; configurable later.
