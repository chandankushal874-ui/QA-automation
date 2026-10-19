# CI/CD Integration Guide — PR-Gated QA Crawls

Gate every PR with a real cross-browser crawl of its preview deployment.
Powered by your self-hosted CBTP instance; costs nothing per run.

---

## 1. Create a dedicated CI user on CBTP

```bash
# On the CBTP host (admin session):
curl -X POST http://<cbtp-host>:8001/api/auth/users \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"username":"ci-bot","password":"<strong-password>","role":"user"}'
```

Why a dedicated user: crawls are tenant-isolated per account, and the CI
user's 2-slot limit prevents CI from flooding the platform.

## 2. Create an API token for the CI user

Login once as `ci-bot`, then:

```bash
curl -X POST http://<cbtp-host>:8001/api/me/tokens \
  -H "Authorization: Bearer <ci-bot-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"name":"github-actions"}'
```

The response contains the token **once** (`cbtp_...`). Save it — the server
stores only its SHA-256 hash.

## 3. Add repo secrets (GitHub → Settings → Secrets → Actions)

| Secret | Value |
|--------|-------|
| `CBTP_API_URL` | `http://<cbtp-host>:8001` (or your nginx domain) |
| `CBTP_API_TOKEN` | the `cbtp_...` value from step 2 |
| `QA_TARGET_URL` | *(optional)* fixed staging URL to crawl |

## 4. Copy the workflow into the target repo

Option A — this repo's PRs already have `.github/workflows/qa-crawl-gate.yml`.

Option B — any other repo can reuse the composite action:

```yaml
jobs:
  qa:
    runs-on: ubuntu-latest
    steps:
      - uses: your-org/cross-browser-testing-platform/.github/actions/qa-gate@main
        with:
          api-url:   ${{ secrets.CBTP_API_URL }}
          api-token: ${{ secrets.CBTP_API_TOKEN }}
          target:    https://my-app-pr-${{ github.event.number }}.vercel.app
```

## 5. What happens on each PR

1. Workflow cancels superseded runs on the same PR (frees slots)
2. `scripts/ci_gate.py` submits a crawl of the PR's preview URL
3. Polls until complete (default 30 min timeout)
4. Gates: **fails the check** if BLOCKING issues > 0 (thresholds configurable)
5. PR comment: ✅/❌ + dashboard link

Exit codes: `0` pass · `1` gate failed · `2` infrastructure error.

## Local test (no GitHub needed)

```bash
python scripts/ci_gate.py \
  --api-url http://127.0.0.1:8001 \
  --token cbtp_<your-token> \
  --target http://fixture:8090 \
  --max-pages 5 --max-depth 2 --timeout 600
```

## Revoking a leaked token

```bash
curl -X DELETE http://<cbtp-host>:8001/api/me/tokens/<token-id> \
  -H "Authorization: Bearer <ci-bot-jwt>"
```
(or flip `is_active` off in the UI) — the next CI run gets 401.

## Threshold tuning

| Goal | Flags |
|------|-------|
| Only block on crashes/JS errors | `--max-blocking 0` (default) |
| Enforce functional quality too | `--max-functional 5` |
| Fast smoke on big sites | `--max-pages 20 --max-depth 2` |
| All three browsers in CI | `--browsers chromium,firefox,webkit` |

---

## Visual-baseline diff gate (optional)

Beyond issue counts, `ci_gate.py` can diff the PR's crawl against a **visual
baseline** and fail the check when screenshots regress.

### 1. Create a project + baseline

```bash
# Create a project (owner-scoped; slug is the CI reference)
curl -X POST http://<cbtp-host>:8001/api/projects \
  -H "Authorization: Bearer <jwt-or-token>" -H "Content-Type: application/json" \
  -d '{"name":"Storefront","slug":"storefront","base_url":"https://shop.example.com"}'

# After a crawl of the *default branch* completes, capture its screenshots
# as the approved baseline:
curl -X POST http://<cbtp-host>:8001/api/projects/<id>/baseline \
  -H "Authorization: Bearer <jwt-or-token>" -H "Content-Type: application/json" \
  -d '{"job_id":"<job-of-main-crawl>"}'
```

### 2. Gate CI on the diff

```bash
python scripts/ci_gate.py \
  --api-url "$CBTP_API_URL" --token "$CBTP_API_TOKEN" \
  --target "https://my-app-pr-42.vercel.app" \
  --project storefront --branch main --max-visual 0 \
  --max-blocking 0
```

- `--project <slug>` enables the visual gate; `--branch` defaults to the
  project's `default_branch`.
- `--max-visual N` fails the check when **> N** visual `regressions` (changes
  beyond the project's pixel threshold, minus ignore regions) are detected.
- `regressions` respect the project's **ignore regions** (dynamic banners,
  timestamps, carousels) — masked pixels never count.

### 3. Review & approve

- The diff result is a persisted compare: `GET /api/projects/<id>/compare/<cid>`
  returns per-URL verdicts + image keys.
- Approve a compare (`POST .../approve`) to promote the new screenshots to the
  active baseline (superseding the old one) — do this after a deliberate,
  accepted visual change.
- Diff/baseline images are served through a tenant-checked artifact proxy:
  `GET /api/projects/<id>/artifact/<key>`.

Exit codes remain: `0` pass · `1` gate failed (issue or visual) · `2` infra error.