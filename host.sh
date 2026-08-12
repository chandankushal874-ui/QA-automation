#!/usr/bin/env bash
# ============================================================================
# Cross-Browser Testing Platform — ONE COMMAND (macOS / Linux)
# ============================================================================
# From the project folder:
#   chmod +x host.sh && ./host.sh
#
# What it does (only this — no Docker):
#   1) Creates .venv
#   2) pip install .
#   3) playwright install chromium firefox webkit
#   4) Writes minimal .env if missing
#   5) Starts API at http://localhost:8000
#   6) Optionally starts UI on :3000 if Node is present
#
# Disk: source (small) + venv (~0.4 GB) + 3 browsers (~2 GB) ≈ 2.5–4 GB total.
# ============================================================================

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PORT="${CBTP_PORT:-8000}"
UI_PORT="${CBTP_UI_PORT:-3000}"
SKIP_UI="${CBTP_SKIP_UI:-0}"

step() { printf '\n==> %s\n' "$1"; }
ok()   { printf '    %s\n' "$1"; }
warn() { printf '    WARNING: %s\n' "$1"; }

echo "============================================================"
echo " CBTP local host (no Docker) — multi-browser"
echo "============================================================"
echo " Project: $ROOT"
echo " Expected disk after install: ~2.5–4 GB (venv + Chromium/Firefox/WebKit)"
echo "============================================================"

step "Checking Python 3.12+"
PY=""
VER=""
find_python() {
  PY=""; VER=""
  for c in python3.12 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      local v
      v="$("$c" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
      case "$v" in
        3.1[2-9]|3.[2-9]*) PY="$c"; VER="$v"; return 0 ;;
      esac
    fi
  done
  return 1
}

install_python() {
  step "Python 3.12+ not found — installing automatically"
  local os="$(uname -s)"
  if [[ "$os" == "Darwin" ]]; then
    if ! command -v brew >/dev/null 2>&1; then
      step "Installing Homebrew (may prompt for your password once)"
      NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || true
      [[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
      [[ -x /usr/local/bin/brew ]] && eval "$(/usr/local/bin/brew shellenv)"
    fi
    brew install python@3.12 || true
    brew link --overwrite python@3.12 || true
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y || true
    if ! sudo apt-get install -y python3.12 python3.12-venv python3-pip; then
      # Older Ubuntu/Debian: python3.12 lives in the deadsnakes PPA
      sudo apt-get install -y software-properties-common || true
      sudo add-apt-repository -y ppa:deadsnakes/ppa || true
      sudo apt-get update -y || true
      sudo apt-get install -y python3.12 python3.12-venv python3-pip || true
    fi
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3.12 python3.12-pip || true
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y python3.12 python3.12-pip || true
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm python python-pip || true
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper install -y python312 python312-pip || true
  fi
}

if ! find_python; then
  install_python
  find_python || true
fi
if [[ -z "$PY" ]]; then
  echo "Python 3.12+ could not be installed automatically." >&2
  echo "Install it manually (macOS: brew install python@3.12 | Ubuntu: apt install python3.12) and re-run." >&2
  exit 1
fi
ok "Using: $PY ($VER)"

step "Creating virtualenv (.venv)"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  "$PY" -m venv "$ROOT/.venv"
fi
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
python -m pip install --upgrade pip setuptools wheel >/dev/null
ok "venv ready"

step "Installing Python package"
pip install --no-cache-dir -e .
ok "Python deps installed"

step "Installing Playwright browsers (chromium + firefox + webkit)"
warn "This is the bulk of disk use (~1.5–2.5 GB). Required for multi-browser."
python -m playwright install chromium firefox webkit || \
  python -m playwright install --with-deps chromium firefox webkit
ok "Browsers ready"

step "Checking PostgreSQL 16+"
if ! command -v psql >/dev/null 2>&1; then
  step "PostgreSQL not found — installing automatically"
  os="$(uname -s)"
  if [[ "$os" == "Darwin" ]]; then
    brew install postgresql@16 || true
    brew services start postgresql@16 || true
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y || true
    sudo apt-get install -y postgresql postgresql-contrib || true
    sudo systemctl start postgresql || true
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y postgresql-server || true
    sudo postgresql-setup --initdb || true
    sudo systemctl start postgresql || true
  fi
fi

if command -v createdb >/dev/null 2>&1; then
  createdb cbtp 2>/dev/null || true
fi

step "Checking Redis 7+"
if ! command -v redis-server >/dev/null 2>&1; then
  step "Redis not found — installing automatically"
  os="$(uname -s)"
  if [[ "$os" == "Darwin" ]]; then
    brew install redis || true
    brew services start redis || true
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y || true
    sudo apt-get install -y redis-server || true
    sudo systemctl start redis-server || true
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y redis || true
    sudo systemctl start redis || true
  fi
fi

step "Preparing .env (configured for PostgreSQL)"
ADMIN_PASS="$(python -c 'import secrets; print(secrets.token_urlsafe(12))')"
JWT_SEC="$(python -c 'import secrets; print(secrets.token_hex(16))')"
cat > "$ROOT/.env" <<EOF
# Auto-generated by host.sh — Local mode with PostgreSQL
ADMIN_USERNAME=admin
ADMIN_PASSWORD=${ADMIN_PASS}
AUTH_DISABLED=false
SEED_USERS=
JWT_SECRET=${JWT_SEC}
REDIS_URL=redis://localhost:6379/0
# Local PostgreSQL database connections
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/cbtp
DATABASE_URL_SYNC=postgresql://postgres:postgres@localhost:5432/cbtp
EOF
ok "Wrote .env configured for PostgreSQL"
GENERATED_ADMIN_PASS="$ADMIN_PASS"

mkdir -p reports baselines data

install_node() {
  step "Node.js not found — installing automatically (optional, for the React UI)"
  local os="$(uname -s)"
  if [[ "$os" == "Darwin" ]]; then
    if ! command -v brew >/dev/null 2>&1; then
      step "Installing Homebrew (may prompt for your password once)"
      NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || true
      [[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
      [[ -x /usr/local/bin/brew ]] && eval "$(/usr/local/bin/brew shellenv)"
    fi
    brew install node || true
  elif command -v apt-get >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - || true
    sudo apt-get install -y nodejs || true
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y nodejs || true
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y nodejs || true
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm nodejs npm || true
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper install -y nodejs20 npm20 || sudo zypper install -y nodejs npm || true
  fi
}

UI_PID=""
if [[ "$SKIP_UI" != "1" && -f "$ROOT/frontend/package.json" ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    install_node
    hash -r 2>/dev/null || true
  fi
  if command -v npm >/dev/null 2>&1; then
    step "Installing frontend deps (optional UI)"
    (
      cd "$ROOT/frontend"
      if [[ ! -d node_modules ]]; then
        npm install --no-fund --no-audit
      fi
      export VITE_API_URL="http://localhost:${PORT}"
      npm run dev -- --host 0.0.0.0 --port "$UI_PORT" &
      echo $! > /tmp/cbtp-ui.pid
    )
    UI_PID="$(cat /tmp/cbtp-ui.pid 2>/dev/null || true)"
    ok "UI starting on http://localhost:${UI_PORT}"
  else
    warn "Node/npm unavailable — running API only (UI skipped)."
  fi
WORKER_PID=""
step "Launching RQ Worker process (listening to Redis)"
"$ROOT/.venv/bin/python" -m src.workers.worker &
WORKER_PID=$!
ok "Worker active and polling Redis queue cbtp:default"

cleanup() {
  if [[ -n "${UI_PID}" ]] && kill -0 "$UI_PID" 2>/dev/null; then
    kill "$UI_PID" 2>/dev/null || true
  fi
  if [[ -n "${WORKER_PID}" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    kill "$WORKER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

step "Starting API on http://localhost:${PORT}"
echo ""
echo "============================================================"
echo " LIVE"
echo "============================================================"
echo "  API:      http://localhost:${PORT}"
echo "  Health:   http://localhost:${PORT}/health"
echo "  Docs:     http://localhost:${PORT}/docs"
if [[ -n "$UI_PID" ]]; then
  echo "  UI:       http://localhost:${UI_PORT}"
  echo "  Login:    http://localhost:${UI_PORT}/login"
else
  echo "  Login:    POST http://localhost:${PORT}/api/auth/login"
  echo "  Register: POST http://localhost:${PORT}/api/auth/register"
fi
if [[ -n "$GENERATED_ADMIN_PASS" ]]; then
  echo ""
  echo "  Bootstrap admin (first run):"
  echo "    username: admin"
  echo "    password: ${GENERATED_ADMIN_PASS}"
fi
echo ""
echo "  Users: register any unique username — each gets isolated crawls."
echo "  Stop:  Ctrl+C"
echo "============================================================"
echo ""

export PYTHONPATH="$ROOT"
exec python -m uvicorn src.api.app:app --host 0.0.0.0 --port "$PORT"
