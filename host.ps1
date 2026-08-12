# ============================================================================
# Cross-Browser Testing Platform - ONE COMMAND (Windows)
# ============================================================================
# From the project folder:
#   powershell -ExecutionPolicy Bypass -File host.ps1
#
# What it does (only this - no Docker):
#   1) Creates .venv
#   2) pip install .  (Python deps)
#   3) playwright install chromium firefox webkit  (multi-browser)
#   4) Writes minimal .env if missing
#   5) Starts API at http://localhost:8000
#   6) Optionally builds UI and serves on :3000 if Node is present
#
# Disk: source zip (small) + venv (~0.4 GB) + 3 browsers (~2 GB) ~ 2.5-4 GB total.
# No Docker Desktop, no 18 GB build cache.
# ============================================================================

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if (-not $Root) { $Root = (Get-Location).Path }
Set-Location -LiteralPath $Root

$Port = if ($env:CBTP_PORT) { [int]$env:CBTP_PORT } else { 8000 }
$UiPort = if ($env:CBTP_UI_PORT) { [int]$env:CBTP_UI_PORT } else { 3000 }
$SkipUi = $env:CBTP_SKIP_UI -eq "1"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

Write-Host "============================================================"
Write-Host " CBTP local host (no Docker) - multi-browser"
Write-Host "============================================================"
Write-Host " Project: $Root"
Write-Host " Expected disk after install: ~2.5-4 GB (venv + Chromium/Firefox/WebKit)"
Write-Host "============================================================"

# --- Python ---
Write-Step "Checking Python 3.12+"

function Refresh-PathFromRegistry {
    # Pick up PATH changes made by installers without opening a new shell
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Find-Python312 {
    $candidates = @(
        @{ Cmd = "py"; Args = @("-3.12") },
        @{ Cmd = "py"; Args = @("-3") },
        @{ Cmd = "python"; Args = @() },
        @{ Cmd = "python3"; Args = @() },
        @{ Cmd = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"; Args = @() },
        @{ Cmd = "C:\Program Files\Python312\python.exe"; Args = @() }
    )
    foreach ($c in $candidates) {
        $exe = Get-Command $c.Cmd -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        try {
            $allArgs = $c.Args + @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
            $out = & $c.Cmd @allArgs 2>$null
            if ($out -match "^3\.(1[2-9]|[2-9]\d)") {
                return @{ Cmd = $c.Cmd; Args = $c.Args; Ver = "$out".Trim() }
            }
        } catch {}
    }
    return $null
}

$py = Find-Python312
if (-not $py) {
    Write-Step "Python 3.12+ not found - installing automatically"
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "    Trying winget (Python.Python.3.12)..."
        & winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
        Refresh-PathFromRegistry
        $py = Find-Python312
    }
    if (-not $py) {
        Write-Host "    Trying official python.org installer..."
        try {
            $installer = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
            Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" -OutFile $installer -UseBasicParsing
            Start-Process -FilePath $installer -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0" -Wait
            Refresh-PathFromRegistry
            $py = Find-Python312
        } catch {
            Write-Warn "python.org installer failed: $_"
        }
    }
}
if (-not $py) {
    throw "Python 3.12+ could not be installed automatically. Install from https://www.python.org/downloads/ (check 'Add python.exe to PATH') and re-run."
}
$pyCmd = $py.Cmd
$pyArgs = $py.Args
$ver = $py.Ver
Write-Ok "Using: $pyCmd $($pyArgs -join ' ') (version $ver)"

# --- venv ---
Write-Step "Creating virtualenv (.venv)"
if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) {
    & $pyCmd @($pyArgs + @("-m", "venv", "$Root\.venv"))
}
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
& $VenvPy -m pip install --upgrade pip setuptools wheel | Out-Null
Write-Ok "venv ready"

# --- deps ---
Write-Step "Installing Python package (this project)"
# Use "python -m pip" rather than the pip.exe shim - the shim can break or lock on Windows
& $VenvPy -m pip install --no-cache-dir -e . 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Write-Ok "Python deps installed"

# --- Playwright multi-browser ---
Write-Step "Installing Playwright browsers (chromium + firefox + webkit)"
Write-Warn "This is the bulk of disk use (~1.5-2.5 GB). Required for multi-browser."
& $VenvPy -m playwright install chromium firefox webkit 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Warn "playwright install returned non-zero; trying with deps..."
    & $VenvPy -m playwright install --with-deps chromium firefox webkit 2>&1 | Out-Host
}
Write-Ok "Browsers ready"

# --- PostgreSQL Setup ---
Write-Step "Checking PostgreSQL 16+"
$pgService = Get-Service postgresql* -ErrorAction SilentlyContinue
if (-not $pgService) {
    Write-Step "PostgreSQL not found - installing automatically via winget"
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        & winget install -e --id PostgreSQL.PostgreSQL.16 --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
        Refresh-PathFromRegistry
        $pgService = Get-Service postgresql* -ErrorAction SilentlyContinue
    }
}
if ($pgService -and $pgService.Status -ne "Running") {
    Write-Step "Starting PostgreSQL service"
    Start-Service $pgService.Name -ErrorAction SilentlyContinue
}
if ($pgService) {
    Write-Ok "PostgreSQL service ($($pgService.Name)) is active"
} else {
    Write-Warn "PostgreSQL installation pending - app will connect to PostgreSQL on localhost:5432"
}

# --- Redis Setup ---
Write-Step "Checking Redis 7+"
$redisService = Get-Service redis* -ErrorAction SilentlyContinue
if (-not $redisService) {
    $memuraiService = Get-Service memurai* -ErrorAction SilentlyContinue
    if ($memuraiService) { $redisService = $memuraiService }
}

if (-not $redisService) {
    Write-Step "Redis not found - installing automatically via winget"
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        & winget install -e --id Memurai.MemuraiDeveloper --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) {
            & winget install -e --id Redis.Redis --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
        }
        Refresh-PathFromRegistry
        $redisService = Get-Service redis* -ErrorAction SilentlyContinue
        if (-not $redisService) { $redisService = Get-Service memurai* -ErrorAction SilentlyContinue }
    }
}

if ($redisService -and $redisService.Status -ne "Running") {
    Write-Step "Starting Redis service"
    Start-Service $redisService.Name -ErrorAction SilentlyContinue
}
if ($redisService) {
    Write-Ok "Redis service ($($redisService.Name)) is active"
} else {
    Write-Warn "Redis service pending - worker will connect on redis://localhost:6379/0"
}

# --- .env ---
Write-Step "Preparing .env (configured for PostgreSQL)"
$envPath = Join-Path $Root ".env"
$adminPass = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 12 | ForEach-Object { [char]$_ })
@"
# Auto-generated by host.ps1 - Local mode with PostgreSQL
ADMIN_USERNAME=admin
ADMIN_PASSWORD=$adminPass
AUTH_DISABLED=false
SEED_USERS=
JWT_SECRET=$([guid]::NewGuid().ToString("N"))
REDIS_URL=redis://localhost:6379/0
# Local PostgreSQL database connections
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/cbtp
DATABASE_URL_SYNC=postgresql://postgres:postgres@localhost:5432/cbtp
"@ | Set-Content -LiteralPath $envPath -Encoding UTF8
Write-Ok "Wrote .env configured for PostgreSQL"
$script:GeneratedAdminPass = $adminPass

# Ensure reports dirs exist
New-Item -ItemType Directory -Path (Join-Path $Root "reports") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Root "baselines") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Root "data") -Force | Out-Null

# --- optional UI ---
$uiProc = $null
if (-not $SkipUi -and (Test-Path "$Root\frontend\package.json")) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        Write-Step "Node.js not found - installing automatically (for the React UI)"
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            & winget install -e --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
            Refresh-PathFromRegistry
            $npm = Get-Command npm -ErrorAction SilentlyContinue
        }
        if (-not $npm) {
            Write-Warn "Node.js auto-install failed - API only. Install Node 20+ from https://nodejs.org/ for the React UI."
        }
    }
    if ($npm) {
        Write-Step "Installing frontend deps (optional UI)"
        Push-Location (Join-Path $Root "frontend")
        try {
            if (-not (Test-Path "node_modules")) {
                npm install --no-fund --no-audit 2>&1 | Out-Host
            }
            $env:VITE_API_URL = "http://localhost:$Port"
            Write-Ok "Starting UI on http://localhost:$UiPort"
            $uiProc = Start-Process -FilePath "npm" -ArgumentList "run","dev","--","--host","0.0.0.0","--port","$UiPort" `
                -WorkingDirectory (Get-Location) -PassThru -WindowStyle Hidden
        } finally {
            Pop-Location
        }
    } else {
        Write-Warn "Node/npm unavailable - running API only (UI skipped)."
    }
}

# --- RQ Worker Process ---
Write-Step "Launching RQ Worker process (listening to Redis cbtp:default)"
$workerProc = Start-Process -FilePath $VenvPy -ArgumentList "-m","src.workers.worker" `
    -WorkingDirectory $Root -PassThru -WindowStyle Hidden
Write-Ok "Worker active and polling Redis queue cbtp:default"

# --- start API ---
Write-Step "Starting API on http://localhost:$Port"
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " LIVE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  API:      http://localhost:$Port"
Write-Host "  Health:   http://localhost:$Port/health"
Write-Host "  Docs:     http://localhost:$Port/docs"
if ($uiProc) {
    Write-Host "  UI:       http://localhost:$UiPort"
    Write-Host "  Login:    http://localhost:$UiPort/login"
} else {
    Write-Host "  Login:    POST http://localhost:$Port/api/auth/login"
    Write-Host "  Register: POST http://localhost:$Port/api/auth/register"
}
if ($script:GeneratedAdminPass) {
    Write-Host ""
    Write-Host "  Bootstrap admin (first run):" -ForegroundColor Yellow
    Write-Host "    username: admin"
    Write-Host "    password: $script:GeneratedAdminPass"
}
Write-Host ""
Write-Host "  Users: register any unique username - each gets isolated crawls."
Write-Host "  Stop:  Ctrl+C"
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""

# Disk summary
try {
    $venvSize = (Get-ChildItem "$Root\.venv" -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    Write-Host ("  Approx .venv size: {0:N1} GB" -f ($venvSize / 1GB))
} catch {}

$env:PYTHONPATH = $Root
& $VenvPy -m uvicorn src.api.app:app --host 0.0.0.0 --port $Port
