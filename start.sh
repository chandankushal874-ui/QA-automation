#!/bin/bash
# ============================================================================
# Cross-Browser Testing Platform — One-Command Startup (Linux/macOS)
# ============================================================================
# Usage:
#   ./start.sh          — start all services (build + up)
#   ./start.sh stop     — stop all services
#   ./start.sh restart  — restart all services
#   ./start.sh logs     — tail logs from all services
#   ./start.sh down     — stop and remove all containers/volumes
#   ./start.sh status   — show status of all services
# ============================================================================

set -e

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.production"

# Check if .env.production exists, fall back to example
if [ ! -f "$ENV_FILE" ]; then
    echo "[WARNING] $ENV_FILE not found. Copying from .env.production.example"
    echo "[WARNING] Edit $ENV_FILE with your production secrets before going live!"
    cp .env.production.example "$ENV_FILE"
fi

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "[ERROR] Docker is not installed or not in PATH."
    echo "[ERROR] Install Docker: https://docs.docker.com/engine/install/"
    exit 1
fi

# Check if Docker daemon is running
if ! docker info &> /dev/null; then
    echo "[ERROR] Docker daemon is not running. Start Docker first."
    exit 1
fi

# Check if docker compose subcommand is available (v2)
if ! docker compose version &> /dev/null; then
    echo "[ERROR] Docker Compose v2 is not available."
    echo "[ERROR] Install it: https://docs.docker.com/compose/install/"
    exit 1
fi

ACTION="${1:-start}"

case "$ACTION" in
    start)
        echo ""
        echo "============================================================"
        echo "  Starting Cross-Browser Testing Platform..."
        echo "============================================================"
        echo ""
        echo "Building images (first run may take 5-10 minutes)..."
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build
        echo ""
        echo "Starting all services..."
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
        echo ""
        echo "============================================================"
        echo "  Platform is starting up!"
        echo "============================================================"
        echo ""
        echo "  Frontend:       http://localhost"
        echo "  API:            http://localhost/api/"
        echo "  Health check:   http://localhost/health"
        echo "  RQ Dashboard:   http://localhost/dashboard/"
        echo "  MinIO Console:  http://localhost:9001"
        echo ""
        echo "  View logs:      ./start.sh logs"
        echo "  Stop:           ./start.sh stop"
        echo "============================================================"
        echo ""
        ;;
    stop)
        echo "Stopping all services..."
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" stop
        ;;
    restart)
        echo "Restarting all services..."
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" restart
        ;;
    logs)
        echo "Tailing logs (Ctrl+C to exit)..."
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" logs -f --tail=50
        ;;
    down)
        echo "Stopping and removing all containers and volumes..."
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down -v
        ;;
    status)
        echo "Service status:"
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps
        ;;
    *)
        echo "Unknown action: $ACTION"
        echo "Usage: ./start.sh [start|stop|restart|logs|down|status]"
        exit 1
        ;;
esac