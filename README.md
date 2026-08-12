# Cross-Browser Testing Platform

**Autonomous Cross-Browser Crawler & Self-Healing Debugger**

Paste a URL. We crawl your entire web app across Chromium, Firefox, and WebKit, triage every error, and generate AI-powered fixes — all in one click.

## Architecture

```
cross-browser-testing-platform/
├── src/                          # Python backend (FastAPI + Playwright)
│   ├── api/app.py                # REST API endpoints
│   ├── crawler/engine.py         # Multi-browser crawl engine
│   ├── analysis/                 # Issue triage & dedup
│   ├── discovery/                # Endpoint & API discovery
│   ├── browser/                  # Playwright browser management
│   ├── models/database.py        # SQLAlchemy data models
│   └── main.py                   # Entry point
├── frontend/                     # React admin panel (Convex SaaS template)
│   ├── convex/                   # Convex backend (auth, testing, stripe)
│   │   ├── schema.ts             # Database schema
│   │   ├── testing.ts            # Test job management functions
│   │   ├── auth.ts               # Authentication (OTP + GitHub)
│   │   └── stripe.ts             # Stripe billing integration
│   └── src/
│       └── routes/
│           └── _app/_auth/dashboard/
│               ├── _layout.index.tsx        # Dashboard overview
│               ├── _layout.new-test.tsx     # New test form
│               ├── _layout.history.tsx      # Test history
│               └── _layout.report.$testJobId.tsx  # Detailed report
└── tests/                        # Unit & integration tests
```

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- Playwright browsers: `playwright install chromium firefox webkit`
- (Optional) PostgreSQL + Redis for production

### 1. Start the Python Backend

```bash
cd cross-browser-testing-platform
pip install -e .
python -m src.main
# API runs on http://localhost:8000
```

### 2. Start the Frontend Admin Panel

```bash
cd frontend
npm install
npx convex dev --run init --until-success
npm run dev
# Frontend runs on http://localhost:5173
```

### 3. Run a Test

1. Open http://localhost:5173
2. Sign in with email OTP or GitHub
3. Click "New Test" → paste a URL
4. Select browsers (Chromium, Firefox, WebKit)
5. Click "Start Cross-Browser Test"
6. View the live report with triaged issues and AI-suggested fixes

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/jobs` | Submit a new cross-browser test |
| GET | `/jobs/{id}` | Get job status |
| GET | `/jobs/{id}/report` | Get full triaged report |
| GET | `/jobs` | List all jobs |
| GET | `/jobs/{id}/dashboard` | HTML report dashboard |
| GET | `/health` | Health check |

## Key Features

- **Multi-Browser Crawling**: Chromium, Firefox, WebKit in parallel
- **Automatic Bug Detection**: JS errors, CORS, CSP, network failures, visual regressions
- **AI-Powered Fixes**: Code patches with confidence scores
- **Auth Bypass**: Handles login flows, SPA state, destructive action gating
- **Endpoint Discovery**: Mines HTML, JS bundles, GraphQL, OpenAPI specs
- **Admin Panel**: Full React dashboard with test history, reports, and billing

## Enterprise Auth (Audit Log, MFA, Real IdP)

### Audit log
Every login, registration, MFA event, OIDC login, job lifecycle event, and admin
action is written to the append-only `audit_events` table. Admins can query it:

```
GET /api/auth/audit?limit=50&offset=0&action=auth.login&actor_username=alice
```

Disable with `AUDIT_LOG_ENABLED=false` (not recommended).

### TOTP MFA (per-user opt-in)
1. `POST /api/auth/mfa/setup` (Bearer) → scan `qr_url` / enter `secret` in Google Authenticator, Authy, etc.
2. `POST /api/auth/mfa/confirm` with a 6-digit code → MFA enabled.
3. Password login then returns `mfa_required: true` + `mfa_token`; complete with `POST /api/auth/mfa/verify`.
4. `POST /api/auth/mfa/disable` (password + code) turns it off. `MFA_ENABLED=false` disables the feature platform-wide.

### Real IdP via OIDC (Google)
1. Google Cloud Console → APIs & Services → Credentials → **Create OAuth 2.0 Client ID** (Web application).
2. Add authorized redirect URI: `http://localhost:8000/api/auth/oidc/callback`.
3. Set in `.env`: `OIDC_ENABLED=true`, `OIDC_ISSUER=https://accounts.google.com`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`.
4. Users sign in at `GET /api/auth/oidc/login` (Authorization Code + PKCE; the `id_token` is signature-verified against Google's JWKS with issuer/audience/nonce checks).
5. On success the browser is redirected to `OIDC_UI_SUCCESS_URL` with the session JWT as `#token=...` (set it empty to get a JSON response instead). First-time IdP users get an account auto-created (`authProvider: "oidc"`); existing accounts are linked by email.
