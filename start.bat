@echo off
REM ============================================================================
REM Cross-Browser Testing Platform — One-Command Startup (Windows)
REM ============================================================================
REM Usage:
REM   start.bat          — start all services (build + up)
REM   start.bat stop     — stop all services
REM   start.bat restart  — restart all services
REM   start.bat logs     — tail logs from all services
REM   start.bat down     — stop and remove all containers/volumes
REM   start.bat status   — show status of all services
REM ============================================================================

setlocal

set COMPOSE_FILE=docker-compose.prod.yml
set ENV_FILE=.env.production

REM Check if .env.production exists, fall back to example
if not exist "%ENV_FILE%" (
    echo [WARNING] %ENV_FILE% not found. Copying from .env.production.example
    echo [WARNING] Edit %ENV_FILE% with your production secrets before going live!
    copy .env.production.example %ENV_FILE% >nul
)

REM Check if Docker is available
where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not installed or not in PATH.
    echo [ERROR] Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/
    exit /b 1
)

REM Check if Docker daemon is running
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker daemon is not running. Start Docker Desktop first.
    exit /b 1
)

set ACTION=%1
if "%ACTION%"=="" set ACTION=start

if "%ACTION%"=="start" (
    echo.
    echo ============================================================
    echo   Starting Cross-Browser Testing Platform...
    echo ============================================================
    echo.
    echo Building images ^(first run may take 5-10 minutes^)...
    docker compose -f %COMPOSE_FILE% --env-file %ENV_FILE% build
    if errorlevel 1 (
        echo [ERROR] Build failed. Check the output above.
        exit /b 1
    )
    echo.
    echo Starting all services...
    docker compose -f %COMPOSE_FILE% --env-file %ENV_FILE% up -d
    if errorlevel 1 (
        echo [ERROR] Startup failed. Check the output above.
        exit /b 1
    )
    echo.
    echo ============================================================
    echo   Platform is starting up!
    echo ============================================================
    echo.
    echo   Frontend:       http://localhost
    echo   API:            http://localhost/api/
    echo   Health check:   http://localhost/health
    echo   RQ Dashboard:   http://localhost/dashboard/
    echo   MinIO Console:  http://localhost:9001
    echo.
    echo   View logs:      start.bat logs
    echo   Stop:           start.bat stop
    echo ============================================================
    echo.
    goto :eof
)

if "%ACTION%"=="stop" (
    echo Stopping all services...
    docker compose -f %COMPOSE_FILE% --env-file %ENV_FILE% stop
    goto :eof
)

if "%ACTION%"=="restart" (
    echo Restarting all services...
    docker compose -f %COMPOSE_FILE% --env-file %ENV_FILE% restart
    goto :eof
)

if "%ACTION%"=="logs" (
    echo Tailing logs ^(Ctrl+C to exit^)...
    docker compose -f %COMPOSE_FILE% --env-file %ENV_FILE% logs -f --tail=50
    goto :eof
)

if "%ACTION%"=="down" (
    echo Stopping and removing all containers and volumes...
    docker compose -f %COMPOSE_FILE% --env-file %ENV_FILE% down -v
    goto :eof
)

if "%ACTION%"=="status" (
    echo Service status:
    docker compose -f %COMPOSE_FILE% --env-file %ENV_FILE% ps
    goto :eof
)

echo Unknown action: %ACTION%
echo Usage: start.bat [start^|stop^|restart^|logs^|down^|status]