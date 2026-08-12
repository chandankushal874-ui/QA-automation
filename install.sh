#!/bin/bash
# ============================================================================
# Cross-Browser Testing Platform — ONE-COMMAND INSTALLER (Linux)
# ============================================================================
# On any fresh Linux server, run ONE of:
#
#   A) Piped from a hosted copy of this script:
#        curl -fsSL https://YOUR-HOST/install.sh | bash
#
#   B) From inside the project folder (e.g. after scp/git clone):
#        chmod +x install.sh && ./install.sh
#
# It installs Docker if needed, downloads the source code (if not already
# present), generates random production passwords, builds all containers,
# starts the platform, and prints the browser link when it is live.
#
# Optional environment variables (set BEFORE bash, e.g. `... | VAR=x bash`):
#   CBTP_TARBALL_URL    URL of cbtp-source.tar.gz (default: embedded below)
#   CBTP_REPO_URL       git URL to clone instead of using the tarball
#   CBTP_DIR            install location            (default: /opt/cbtp)
#   OPENROUTER_API_KEY  enables AI fix suggestions  (default: disabled)
#   WORKER_REPLICAS     concurrent crawls           (default: 4)
#   PROXY_PORT          web port                    (default: 80)
# ============================================================================

set -e

# ── EDIT THIS after you upload the tarball somewhere ──
DEFAULT_TARBALL_URL="https://YOUR-HOST/cbtp-source.tar.gz"

CBTP_DIR="${CBTP_DIR:-/opt/cbtp}"
TARBALL_URL="${CBTP_TARBALL_URL:-$DEFAULT_TARBALL_URL}"
REPO_URL="${CBTP_REPO_URL:-}"
WORKER_REPLICAS="${WORKER_REPLICAS:-4}"
PROXY_PORT="${PROXY_PORT:-80}"

# Use sudo only when not already root
SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

echo "============================================================"
echo " Cross-Browser Testing Platform — automatic install"
echo "============================================================"

# ── 1. Docker ────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "==> [1/5] Installing Docker..."
    curl -fsSL https://get.docker.com | $SUDO sh
else
    echo "==> [1/5] Docker already installed: $(docker --version)"
fi
if ! docker info &>/dev/null; then
    echo "==> Starting Docker daemon..."
    $SUDO systemctl enable --now docker 2>/dev/null || $SUDO service docker start
fi

# ── 2. Source code ───────────────────────────────────────────
if [ -f "docker-compose.prod.yml" ]; then
    PROJECT_DIR="$(pwd)"
    echo "==> [2/5] Running inside the project folder — no download needed"
elif [ -f "$CBTP_DIR/docker-compose.prod.yml" ]; then
    PROJECT_DIR="$CBTP_DIR"
    echo "==> [2/5] Source already present at $CBTP_DIR"
else
    echo "==> [2/5] Downloading source code..."
    if [ -n "$REPO_URL" ]; then
        command -v git &>/dev/null || { $SUDO apt-get update && $SUDO apt-get install -y git; }
        $SUDO git clone "$REPO_URL" "$CBTP_DIR"
    else
        $SUDO mkdir -p "$CBTP_DIR"
        curl -fsSL "$TARBALL_URL" | $SUDO tar xz -C "$CBTP_DIR" --strip-components=1
    fi
    $SUDO chown -R "$(id -u):$(id -g)" "$CBTP_DIR" 2>/dev/null || true
    PROJECT_DIR="$CBTP_DIR"
fi
cd "$PROJECT_DIR"

# ── 3. Configuration (.env.production) ───────────────────────
if [ ! -f .env.production ]; then
    echo "==> [3/5] Generating .env.production with random passwords..."
    cp .env.production.example .env.production
    sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 16)|" .env.production
    sed -i "s|^S3_SECRET_KEY=.*|S3_SECRET_KEY=$(openssl rand -hex 16)|" .env.production
    sed -i "s|^WORKER_REPLICAS=.*|WORKER_REPLICAS=$WORKER_REPLICAS|" .env.production
    sed -i "s|^PROXY_PORT=.*|PROXY_PORT=$PROXY_PORT|" .env.production
    if [ -n "$OPENROUTER_API_KEY" ]; then
        sed -i "s|^OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=$OPENROUTER_API_KEY|" .env.production
    else
        echo "    (no OPENROUTER_API_KEY set — AI fix suggestions will be disabled;"
        echo "     add it later in $PROJECT_DIR/.env.production and run ./start.sh restart)"
    fi
else
    echo "==> [3/5] .env.production already exists — keeping it"
fi

# ── 4. Build + start everything ──────────────────────────────
echo "==> [4/5] Building images and starting all services (first run: 5-10 min)..."
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build

# ── 5. Wait until live, then print the link ──────────────────
echo "==> [5/5] Waiting for the platform to come up..."
LIVE=0
for _ in $(seq 1 60); do
    if curl -fsS -m 2 http://localhost:8000/health &>/dev/null; then LIVE=1; break; fi
    sleep 5
done

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
IP=${IP:-localhost}
PORT_SUFFIX=""
[ "$PROXY_PORT" != "80" ] && PORT_SUFFIX=":$PROXY_PORT"

echo ""
if [ "$LIVE" = "1" ]; then
    echo "============================================================"
    echo " DONE — the platform is LIVE. Open in your browser:"
    echo ""
    echo "   Dashboard : http://$IP$PORT_SUFFIX"
    echo "   API docs  : http://$IP$PORT_SUFFIX/api/docs"
    echo "   Health    : http://$IP$PORT_SUFFIX/health"
    echo ""
    echo " Manage it:  cd $PROJECT_DIR"
    echo "   ./start.sh status | logs | stop | restart"
    echo "============================================================"
else
    echo "[WARNING] Services started but the API is not answering yet."
    echo "          Check with: cd $PROJECT_DIR && ./start.sh logs"
fi
