# Cross-Browser Testing Platform — Deployment Handoff

## What's in this package

This package contains the **source code** for the entire platform. Docker will
build everything from this source — no pre-built images needed.

```
cross-browser-testing-platform/
├── src/                      # Python backend (FastAPI, crawler, workers, analysis)
├── frontend/                 # React + Vite frontend (source code)
├── fixture_app/              # Test fixture app
├── nginx/                    # Nginx reverse proxy config
├── alembic/                  # Database migrations
├── tests/                    # Test suite
├── scripts/                  # Utility scripts
├── Dockerfile                # Builds the Python backend + worker image
├── frontend/Dockerfile       # Builds the frontend static image
├── docker-compose.prod.yml   # Orchestrates ALL 9 services + 20 workers
├── pyproject.toml            # Python dependencies
├── .env.production.example   # Template — copy to .env.production and fill in
├── DEPLOYMENT.md             # Quick deployment guide
└── README.md                 # Project overview
```

## Prerequisites on the server

1. **Docker Engine** (v24+) — https://docs.docker.com/engine/install/
2. **Docker Compose** (v2+) — usually bundled with Docker Engine
3. **Linux server** (Ubuntu 22.04+ recommended) — x86_64 architecture
4. **Minimum specs** for 20 concurrent crawls:
   - RAM: 32 GB (20 workers × ~1.5 GB each for Playwright browsers)
   - CPU: 8+ cores
   - Disk: 50 GB (for Docker images + crawl artifacts)
   - Ports: 80 (nginx), 9001 (MinIO console) — or behind a load balancer

## Deployment steps

```bash
# 1. Copy this package to the server and unzip
unzip cross-browser-testing-platform-SHAREABLE.zip
cd cross-browser-testing-platform

# 2. Create your production environment file
cp .env.production.example .env.production
nano .env.production   # Fill in YOUR secrets (DB passwords, API keys)

# 3. Build and start everything (this builds the ~4 GB images from source)
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build

# 4. Wait ~5-10 minutes for the first build, then verify
docker ps                           # All containers should show "Up"
curl http://localhost/health        # Should return {"status":"healthy",...}
```

## Access URLs (after deployment)

| URL | Service |
|-----|---------|
| http://YOUR-SERVER/ | Frontend (React app) |
| http://YOUR-SERVER/health | API health check |
| http://YOUR-SERVER/docs | FastAPI Swagger docs |
| http://YOUR-SERVER/dashboard/ | RQ job queue dashboard |
| http://YOUR-SERVER:9001 | MinIO console (artifacts) |

## What you need to configure in `.env.production`

| Variable | Description | Example |
|----------|-------------|---------|
| `POSTGRES_PASSWORD` | Database password (SET YOUR OWN) | `your_strong_password_here` |
| `S3_SECRET_KEY` | MinIO password (SET YOUR OWN) | `your_minio_password_here` |
| `OPENROUTER_API_KEY` | OpenRouter API key (optional — mock fixes if blank) | `sk-or-v1-...` |
| `PROXY_PORT` | Port for nginx entry point | `80` |
| `GITHUB_TOKEN` | GitHub token (optional) | `ghp_...` |
| `E2B_API_KEY` | E2B sandbox key (optional) | `e2b_...` |
| `FIRECRAWL_API_KEY` | Firecrawl key (optional) | `fc-...` |

**Important:** Never commit `.env.production` to version control. Keep your
secrets safe.

## Managing the deployment

```bash
# View all running containers
docker compose -f docker-compose.prod.yml ps

# View logs
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml logs -f worker

# Stop everything
docker compose -f docker-compose.prod.yml down

# Restart after code changes (rebuild + restart)
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

## Architecture

```
                    ┌─────────────┐
   Users ─────────► │ nginx-proxy │ :80
                    └──────┬──────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌──────────┐    ┌──────────┐    ┌──────────────┐
    │ Frontend │    │   API    │    │ RQ Dashboard │
    │ (nginx)  │    │ (FastAPI)│    │              │
    └──────────┘    └────┬─────┘    └──────────────┘
                          │
                    ┌─────┴─────┐
                    │  20 RQ    │  ← 20 concurrent crawl workers
                    │  Workers  │     (Playwright browsers)
                    └─────┬─────┘
                          │
           ┌──────────────┼──────────────┐
           ▼              ▼              ▼
    ┌──────────┐   ┌──────────┐   ┌──────────┐
    │PostgreSQL│   │  Redis   │   │  MinIO   │
    │   :5432  │   │  :6379   │   │  :9000   │
    └──────────┘   └──────────┘   └──────────┘
```

## Support

See `DEPLOYMENT.md` for the quick-start guide and `README.md` for project overview.