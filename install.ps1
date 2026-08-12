# ============================================================================
# Cross-Browser Testing Platform — ONE-COMMAND INSTALLER (Windows)
# ============================================================================
# On any Windows server/PC with PowerShell, run ONE of:
#
#   A) Piped from a hosted copy of this script:
#        irm https://YOUR-HOST/install.ps1 | iex
#
#      With options set first:
#        $env:OPENROUTER_API_KEY = "sk-your-key"
#        $env:WORKER_REPLICAS = "8"
#        irm https://YOUR-HOST/install.ps1 | iex
#
#   B) From inside the project folder (e.g. after copying the files over):
#        powershell -ExecutionPolicy Bypass -File install.ps1
#
# It installs Docker Desktop if needed, downloads the source code (if not
# already present), generates random production passwords, builds all
# containers, starts the platform, and prints the browser link when live.
#
# Optional environment variables:
#   CBTP_TARBALL_URL    URL of cbtp-source.tar.gz (default: embedded below)
#   CBTP_REPO_URL       git URL to clone instead of using the tarball
#   CBTP_DIR            install location            (default: C:\cbtp)
#   OPENROUTER_API_KEY  enables AI fix suggestions  (default: disabled)
#   WORKER_REPLICAS     concurrent crawls           (default: 4)
#   PROXY_PORT          web port                    (default: 80)
# ============================================================================

$ErrorActionPreference = "Stop"

# -- EDIT THIS after you upload the tarball somewhere --
$DefaultTarballUrl = "https://YOUR-HOST/cbtp-source.tar.gz"

$InstallDir  = if ($env:CBTP_DIR)          { $env:CBTP_DIR }          else { "C:\cbtp" }
$TarballUrl  = if ($env:CBTP_TARBALL_URL)  { $env:CBTP_TARBALL_URL }  else { $DefaultTarballUrl }
$RepoUrl     = $env:CBTP_REPO_URL
$Replicas    = if ($env:WORKER_REPLICAS)   { $env:WORKER_REPLICAS }   else { "4" }
$ProxyPort   = if ($env:PROXY_PORT)        { $env:PROXY_PORT }        else { "80" }

Write-Host "============================================================"
Write-Host " Cross-Browser Testing Platform - automatic install (Windows)"
Write-Host "============================================================"

# -- 0. WSL2 prerequisite (Docker Desktop on Windows needs it) ----------------
# Docker Desktop's Linux engine runs on WSL2. If WSL2 isn't enabled, Docker
# installs but its engine never starts (the "npipe docker_engine not found"
# error). We enable it here BEFORE installing Docker, so the first run works.
function Test-IsAdmin {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object System.Security.Principal.WindowsPrincipal($id)).IsInRole(
        [System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Enable-WSL2 {
    Write-Host "==> [0/5] Checking WSL2 (required by Docker Desktop)..."

    # a) Windows feature: VirtualMachinePlatform + Microsoft-Windows-Subsystem-Linux
    $vmFeat      = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform      -ErrorAction SilentlyContinue
    $wslFeat     = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
    $needReboot  = $false

    if ($vmFeat -and $vmFeat.State -ne "Enabled") {
        Write-Host "    Enabling Windows feature: VirtualMachinePlatform"
        if (Test-IsAdmin) {
            Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart -ErrorAction SilentlyContinue | Out-Null
            $needReboot = $true
        } else {
            Write-Host "    WARNING: not running as admin - cannot enable VirtualMachinePlatform." -ForegroundColor Yellow
            Write-Host "    Run this script from an Administrator PowerShell, or enable it manually:" -ForegroundColor Yellow
            Write-Host "      dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart" -ForegroundColor Yellow
        }
    }
    if ($wslFeat -and $wslFeat.State -ne "Enabled") {
        Write-Host "    Enabling Windows feature: Microsoft-Windows-Subsystem-Linux"
        if (Test-IsAdmin) {
            Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart -ErrorAction SilentlyContinue | Out-Null
            $needReboot = $true
        } else {
            Write-Host "    WARNING: not running as admin - cannot enable Subsystem-Linux." -ForegroundColor Yellow
            Write-Host "    Run this script from an Administrator PowerShell, or enable it manually:" -ForegroundColor Yellow
            Write-Host "      dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart" -ForegroundColor Yellow
        }
    }

    # b) WSL kernel + default version 2 (needs the 'wsl' command, Win10 2004+ / Win11)
    $wslExe = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if ($wslExe) {
        Write-Host "    Updating WSL kernel and setting default version to 2..."
        $ErrorActionPreference = "Continue"
        wsl --install --no-distribution 2>&1 | Out-Host
        wsl --set-default-version 2 2>&1 | Out-Host
        $ErrorActionPreference = "Stop"
    } else {
        Write-Host "    'wsl.exe' not found - installing WSL via winget..."
        $ErrorActionPreference = "Continue"
        winget install -e --id Microsoft.WSL --accept-source-agreements --accept-package-agreements 2>&1 | Out-Host
        $ErrorActionPreference = "Stop"
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path","User")
        if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
            wsl --set-default-version 2 2>&1 | Out-Host
        }
    }

    if ($needReboot) {
        Write-Host ""
        Write-Host "============================================================" -ForegroundColor Yellow
        Write-Host " WSL2 features were enabled but a REBOOT is required." -ForegroundColor Yellow
        Write-Host "============================================================" -ForegroundColor Yellow
        Write-Host " Reboot the laptop, then re-run this script. It will skip"
        Write-Host " the steps it has already done and continue from Docker."
        Write-Host ""
        $ans = Read-Host "Reboot now? (y/N)"
        if ($ans -match "^[yY]") {
            Restart-Computer -Force
        } else {
            throw "Reboot required for WSL2. Reboot, then re-run: powershell -ExecutionPolicy Bypass -File install.ps1"
        }
    } else {
        Write-Host "    WSL2 is ready."
    }
}

# Only run the WSL2 setup if Docker is NOT already working.
# (If Docker works, WSL2 is obviously already fine.)
$dockerAlreadyWorks = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $ErrorActionPreference = "Continue"
    docker info *>$null
    $ErrorActionPreference = "Stop"
    if ($LASTEXITCODE -eq 0) { $dockerAlreadyWorks = $true }
}
if (-not $dockerAlreadyWorks) {
    Enable-WSL2
}

# -- 1. Docker Desktop -------------------------------------------------------
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Host "==> [1/5] Docker already installed: $(docker --version)"
} else {
    Write-Host "==> [1/5] Installing Docker Desktop via winget..."
    winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
    Write-Host "    Docker Desktop installed. If 'docker' is not found later in this"
    Write-Host "    script, log off and back on (or reboot), then re-run it."
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
}

# Wait for the Docker engine (Docker Desktop can take a minute to start)
Write-Host "==> Waiting for Docker engine..."
if (-not (Get-Process "com.docker*" -ErrorAction SilentlyContinue)) {
    Write-Host "    Starting Docker Desktop..."
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe" -ErrorAction SilentlyContinue
}
$engineUp = $false
foreach ($i in 1..60) {
    try {
        # EAP=Stop would turn docker's stderr into a terminating error —
        # force Continue locally and catch anything that still throws.
        $ErrorActionPreference = "Continue"
        docker info *>$null
        $ErrorActionPreference = "Stop"
        if ($LASTEXITCODE -eq 0) { $engineUp = $true; break }
    } catch {
        $ErrorActionPreference = "Stop"
    }
    if ($i -eq 6) {
        Write-Host "    Still waiting... If this is the first run, open 'Docker Desktop'"
        Write-Host "    from the Start menu and finish its setup (accept terms, WSL2,"
        Write-Host "    possible reboot), then re-run this script."
    }
    Start-Sleep -Seconds 5
}
if (-not $engineUp) {
    throw "Docker engine is not running. Open Docker Desktop, wait for 'Docker Desktop is running', then re-run this script."
}

# -- 2. Source code ----------------------------------------------------------
if (Test-Path "docker-compose.prod.yml") {
    $ProjectDir = (Get-Location).Path
    Write-Host "==> [2/5] Running inside the project folder - no download needed"
} elseif (Test-Path (Join-Path $InstallDir "docker-compose.prod.yml")) {
    $ProjectDir = $InstallDir
    Write-Host "==> [2/5] Source already present at $InstallDir"
} else {
    Write-Host "==> [2/5] Locating source code..."
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    if ($RepoUrl) {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                        [System.Environment]::GetEnvironmentVariable("Path","User")
        }
        git clone $RepoUrl $InstallDir
    } else {
        # --- Look for a LOCAL copy of the tarball first (USB / WhatsApp / manual copy) ---
        # Searches: script's own folder, Desktop, Downloads, C:\cbtp, current dir.
        $localTarball = $null
        $searchPaths = @(
            (Split-Path -Parent $MyInvocation.MyCommand.Path),
            "$env:USERPROFILE\Desktop",
            "$env:USERPROFILE\Downloads",
            $InstallDir,
            (Get-Location).Path
        )
        foreach ($p in $searchPaths) {
            $candidate = Join-Path $p "cbtp-source.tar.gz"
            if (Test-Path $candidate) {
                $sz = (Get-Item $candidate).Length
                if ($sz -gt 100000) {
                    $localTarball = $candidate
                    Write-Host "    Found local tarball: $candidate ($sz bytes)"
                    break
                } else {
                    Write-Host "    WARNING: ignoring $candidate (only $sz bytes - corrupt copy)"
                }
            }
        }

        if ($localTarball) {
            # Extract from the local copy - no internet needed.
            # tar.exe ships with Windows 10/11 and Server 2019+ (bsdtar)
            tar -xzf $localTarball -C $InstallDir --strip-components=1
            if ($LASTEXITCODE -ne 0) { throw "Extraction of $localTarball failed (exit $LASTEXITCODE)." }
        }
        elseif ($TarballUrl -and $TarballUrl -notmatch "YOUR-HOST") {
            # A real URL was provided via CBTP_TARBALL_URL - download it.
            Write-Host "    Downloading from: $TarballUrl"
            $tmp = Join-Path $env:TEMP "cbtp-source.tar.gz"
            Invoke-WebRequest -Uri $TarballUrl -OutFile $tmp
            tar -xzf $tmp -C $InstallDir --strip-components=1
            if ($LASTEXITCODE -ne 0) { throw "Extraction of downloaded tarball failed (exit $LASTEXITCODE)." }
            Remove-Item $tmp -Force
        }
        else {
            # No local tarball and no real URL - tell the user exactly what to do.
            Write-Host ""
            Write-Host "============================================================" -ForegroundColor Red
            Write-Host " ERROR: No source code found and no download URL configured." -ForegroundColor Red
            Write-Host "============================================================" -ForegroundColor Red
            Write-Host ""
            Write-Host " To fix this, do ONE of the following on this machine:"
            Write-Host ""
            Write-Host "   1. Copy 'cbtp-source.tar.gz' to one of these folders:"
            Write-Host "        - Desktop   ($env:USERPROFILE\Desktop)"
            Write-Host "        - Downloads ($env:USERPROFILE\Downloads)"
            Write-Host "        - C:\cbtp"
            Write-Host "      Then re-run this script. It will find and extract it."
            Write-Host ""
            Write-Host "   2. OR set a real download URL and re-run:"
            Write-Host "        `$env:CBTP_TARBALL_URL = 'https://your-real-host/cbtp-source.tar.gz'"
            Write-Host "        powershell -ExecutionPolicy Bypass -File install.ps1"
            Write-Host ""
            Write-Host "   3. OR set a git repo URL and re-run:"
            Write-Host "        `$env:CBTP_REPO_URL = 'https://github.com/you/cross-browser-testing-platform.git'"
            Write-Host "        powershell -ExecutionPolicy Bypass -File install.ps1"
            Write-Host ""
            throw "No source code available. See instructions above."
        }
    }
    $ProjectDir = $InstallDir
}
Set-Location $ProjectDir

# -- 3. Configuration (.env.production) --------------------------------------
function New-RandomHex([int]$Bytes = 16) {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $buf = New-Object byte[] $Bytes
    $rng.GetBytes($buf)
    ($buf | ForEach-Object { $_.ToString("x2") }) -join ""
}

if (-not (Test-Path ".env.production")) {
    Write-Host "==> [3/5] Generating .env.production with random passwords..."
    $pgPass  = New-RandomHex
    $s3Pass  = New-RandomHex
    $content = Get-Content ".env.production.example"
    $content = $content -replace "^POSTGRES_PASSWORD=.*", "POSTGRES_PASSWORD=$pgPass"
    $content = $content -replace "^S3_SECRET_KEY=.*",      "S3_SECRET_KEY=$s3Pass"
    $content = $content -replace "^WORKER_REPLICAS=.*",    "WORKER_REPLICAS=$Replicas"
    $content = $content -replace "^PROXY_PORT=.*",         "PROXY_PORT=$ProxyPort"
    if ($env:OPENROUTER_API_KEY) {
        $content = $content -replace "^OPENROUTER_API_KEY=.*", "OPENROUTER_API_KEY=$($env:OPENROUTER_API_KEY)"
    } else {
        Write-Host "    (no OPENROUTER_API_KEY set - AI fix suggestions will be disabled;"
        Write-Host "     add it later in $ProjectDir\.env.production and run: docker compose -f docker-compose.prod.yml up -d)"
    }
    $content | Set-Content ".env.production" -Encoding ascii
} else {
    Write-Host "==> [3/5] .env.production already exists - keeping it"
}

# -- 4. Build + start everything ---------------------------------------------
# deploy.replicas is Swarm-only (ignored on Docker Desktop). Always use --scale.
# Cap workers on laptops: default $Replicas is 4, but force 1 if machine has <16 GB RAM.
$scaleWorkers = $Replicas
try {
    $totalRamGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 0)
    if ($totalRamGB -gt 0 -and $totalRamGB -lt 16 -and [int]$Replicas -gt 2) {
        Write-Host "    Detected ${totalRamGB} GB RAM - using 1 crawl worker (laptop-safe)."
        $scaleWorkers = "1"
    }
} catch { }

Write-Host "==> [4/5] Building images and starting all services (first run: 5-15 min)..."
Write-Host "    Worker scale: $scaleWorkers  |  Proxy port: $ProxyPort"
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build --scale "worker=$scaleWorkers"
if ($LASTEXITCODE -ne 0) { throw "docker compose failed - scroll up for the error." }

# -- 5. Wait until live, then print the link ---------------------------------
Write-Host "==> [5/5] Waiting for the platform to come up..."
$live = $false
$healthUrls = @(
    "http://localhost:8000/health",
    "http://localhost:$ProxyPort/health",
    "http://127.0.0.1:8000/health"
)
foreach ($i in 1..60) {
    foreach ($u in $healthUrls) {
        try {
            $r = Invoke-WebRequest -Uri $u -TimeoutSec 2 -UseBasicParsing
            if ($r.StatusCode -eq 200) { $live = $true; break }
        } catch { }
    }
    if ($live) { break }
    Start-Sleep -Seconds 5
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
       Select-Object -First 1).IPAddress
if (-not $ip) { $ip = "localhost" }
$portSuffix = if ($ProxyPort -ne "80") { ":$ProxyPort" } else { "" }

Write-Host ""
if ($live) {
    Write-Host "============================================================"
    Write-Host " DONE - the platform is LIVE. Open in your browser:"
    Write-Host ""
    Write-Host "   Dashboard : http://$ip$portSuffix"
    Write-Host "   API docs  : http://$ip$portSuffix/api/docs"
    Write-Host "   Health    : http://$ip$portSuffix/health"
    Write-Host ""
    Write-Host " Manage it:  cd $ProjectDir"
    Write-Host "   docker compose -f docker-compose.prod.yml ps"
    Write-Host "   docker compose -f docker-compose.prod.yml logs -f"
    Write-Host "============================================================"
} else {
    Write-Host "[WARNING] Services started but the API is not answering yet."
    Write-Host "          Check with: cd $ProjectDir ; docker compose -f docker-compose.prod.yml logs -f"
}
