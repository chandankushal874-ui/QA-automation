# 🚀 Deployment Guide

Host the entire QA platform with **one command**.

---

## Prerequisites

1. **Install Docker** — [Docker Desktop](https://docs.docker.com/desktop/) (Windows/macOS) or `curl -fsSL https://get.docker.com | sh` (Linux)
2. **Server:** 8+ CPU cores, 32+ GB RAM, 50+ GB disk (for 20 concurrent crawls)

Verify:
```bash
docker --version
docker compose version
```

---

## Quick Start (3 Steps)

### 1. Configure

```bash
cp .env.production.example .env.production
nano .env.production   # set POSTGRES_PASSWORD, S3_SECRET_KEY, OPENROUTER_API_KEY
```

### 2. Start

**Windows:**
```cmd
start.bat
```

**Linux/macOS:**
```bash
chmod +x start.sh
./start.sh
```

First build takes 5-10 minutes. Subsequent starts take ~10 seconds.

### 3. Access

| Service | URL |
|---------|-----|
| Frontend | http://localhost |
| API docs | http://localhost/api/docs |
| Health | http://localhost/health |
| RQ Dashboard | http://localhost/dashboard/ |
| MinIO Console | http://localhost:9001 |

---

## Management Commands

| Command | Action |
|---------|--------|
| `start.bat` / `./start.sh` | Start |
| `start.bat stop` / `./start.sh stop` | Stop |
| `start.bat restart` / `./start.sh restart` | Restart |
| `start.bat logs` / `./start.sh logs` | View logs |
| `start.bat status` / `./start.sh status` | Show status |
| `start.bat down` / `./start.sh down` | Stop + remove all data ⚠️ |

---

## Deploy to Remote Server

```bash
ssh user@your-server-ip
git clone <your-repo-url> /opt/cbtp && cd /opt/cbtp
cp .env.production.example .env.production
nano .env.production
./start.sh
```

---

## Production Hardening

**Firewall** (only expose 80/443):
```bash
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable
```

**HTTPS** (Let's Encrypt):
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

**Backups** (add to crontab):
```bash
0 2 * * * docker exec cbtp-postgres pg_dump -U cbtp cbtp > /backups/db_$(date +\%Y\%m\%d).sql
```

## How concurrency actually works

- Every submitted job goes into the **Redis/RQ queue** (`POST /jobs` enqueues it; if Redis is down, the API falls back to an in-process thread so local dev still works).
- Each **worker replica runs one crawl at a time** with a real Playwright browser (~1–1.5 GB RAM per worker). Concurrency = replica count.
- Example: 20 users × 5 active crawls = 100 jobs. With `WORKER_REPLICAS=20`, 20 run in parallel and 80 wait in the queue — every user's jobs stay active and drain in order. Nothing is dropped.
- RAM guide: 20 workers ≈ 30 GB · 40 workers ≈ 60 GB. For 100 *simultaneous* crawls you'd need ~150 GB across one big server or several machines each running this compose file with a shared Postgres/Redis.

**Scale workers** — set in `.env.production`:
```bash
WORKER_REPLICAS=20   # 1 replica = 1 concurrent crawl
```
Then: `./start.sh restart`

Watch the queue drain in real time at http://localhost/rq-dashboard/ or `GET /metrics` (`queue_depth`, `active_workers`).

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Port 80 in use | Set `PROXY_PORT=8080` in `.env.production` |
| Docker daemon not running | `sudo systemctl start docker` (Linux) or launch Docker Desktop |
| Build out of memory | Edit Dockerfile: `playwright install --with-deps chromium` only |
| Worker not processing | Check `docker logs cbtp-worker --tail 50` |
| Reports not persisting | `mkdir -p reports && ./start.sh restart` |

---

## Architecture

```
         Nginx (:80) — single entry point
        ┌────────┼────────┬─────────┐
        ▼        ▼        ▼         ▼
   Frontend   API    RQ Dashboard
   (:3000)   (:8000)   (:9181)
                │
        ┌───────┼───────┐
        ▼       ▼       ▼
   PostgreSQL  Redis   MinIO
    (:5432)   (:6379)  (:9000)
                │
           ┌────┴────┐
           ▼         ▼
         Worker   Fixture
```

**Before:** 4-5 terminals · **After:** 1 command
