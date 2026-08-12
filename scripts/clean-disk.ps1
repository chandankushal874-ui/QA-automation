# Free disk space without breaking the working multi-browser stack.
# Safe: removes crawl artifacts, caches, and Docker build cache.
# Does NOT remove source, .venv, node_modules, or running DB volumes.

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path (Join-Path $Root "docker-compose.prod.yml"))) {
    $Root = (Get-Location).Path
}

Write-Host "==> Cleaning local artifacts under $Root"

foreach ($rel in @("reports", "baselines", ".pytest_cache", ".ruff_cache", ".mypy_cache")) {
    $p = Join-Path $Root $rel
    if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "    removed $rel"
    }
}

Get-ChildItem -LiteralPath $Root -Filter "*.db" -File -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force; Write-Host "    removed $($_.Name)" }

Get-ChildItem -LiteralPath $Root -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Path (Join-Path $Root "reports") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Root "baselines") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Root "data") -Force | Out-Null

if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Host "==> Pruning Docker build cache (images/containers kept)"
    docker builder prune -af 2>&1 | Out-Host
    docker image prune -f 2>&1 | Out-Host
    Write-Host "==> Docker disk:"
    docker system df 2>&1 | Out-Host
}

Write-Host "Done. Multi-browser stack unchanged (Chromium+Firefox+WebKit)."
