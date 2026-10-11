# OMNI-PHARM launcher: Ollama (local LLM) + FastAPI (API + built React UI) on one port.
# Usage:  powershell -ExecutionPolicy Bypass -File .\start_servers.ps1          (this PC only)
#         powershell -ExecutionPolicy Bypass -File .\start_servers.ps1 -Lan     (also reachable from LAN)
#         -NoBrowser  do not open a browser window
# Stop:   powershell -ExecutionPolicy Bypass -File .\stop_servers.ps1
param([switch]$Lan, [int]$Port = 8000, [switch]$NoBrowser)
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$url = "http://localhost:$Port"
# Self-contained install: Python, Ollama (+ model files) and Node live in .\runtime, so the whole folder can be
# moved to another PC / drive. Fall back to system installs only when the bundled copy is missing.
$rt = Join-Path $root "runtime"
$ollama = Join-Path $rt "ollama\ollama.exe"
if (-not (Test-Path $ollama)) { $ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe" }
$models = Join-Path $rt "ollama-models"
if (Test-Path (Join-Path $rt "node\node.exe")) { $env:PATH = (Join-Path $rt "node") + ";" + $env:PATH }
$pyHome = Join-Path $rt "python"
$cfg = Join-Path $root ".venv\pyvenv.cfg"
if ((Test-Path (Join-Path $pyHome "python.exe")) -and (Test-Path $cfg) -and
    -not (Select-String -Path $cfg -Pattern ("^home = " + [regex]::Escape($pyHome) + "$") -Quiet)) {
    # folder moved (other drive letter / PC): point the virtual environment at the bundled Python again
    @("home = $pyHome", "include-system-site-packages = false", "version = 3.11.9",
      "executable = $pyHome\python.exe") | Set-Content $cfg -Encoding ascii
    Write-Host "Python path updated for this location: $pyHome" -ForegroundColor Cyan
}

function Launch($cmd) {
    # Win32_Process.Create detaches the process so it survives closing this window
    Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine = $cmd; CurrentDirectory = $root } | Out-Null
}

if (-not (Test-Path $py)) {
    Write-Host "Python environment (.venv) not found. See README section 'Install'." -ForegroundColor Red; exit 1
}
foreach ($f in "data\processed\drug_master.parquet", "artifacts\v2\pair_metrics.json") {
    if (-not (Test-Path (Join-Path $root $f))) {
        Write-Host "Missing $f - build the data/models first (README section 'Data pipeline')." -ForegroundColor Red; exit 1
    }
}

# already running? just open it
try {
    Invoke-RestMethod "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 2 | Out-Null
    Write-Host "OMNI-PHARM is already running: $url" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $url }
    exit 0
} catch { }

if (-not (Test-Path (Join-Path $root "frontend\dist\index.html"))) {
    Write-Host "UI build not found - building (npm run build)..." -ForegroundColor Yellow
    Push-Location (Join-Path $root "frontend"); npm install --no-audit --no-fund; npm run build; Pop-Location
}
if (Test-Path $ollama) {
    # an Ollama that is already running but does not see our model folder (e.g. a system install) is replaced
    $llm = "gemma4:12b"
    $envFile = Join-Path $root ".env"
    if (Test-Path $envFile) {
        $m = Select-String -Path $envFile -Pattern '^OMNIPHARM_LLM_MODEL=(.+)$' | Select-Object -First 1
        if ($m) { $llm = $m.Matches[0].Groups[1].Value.Trim() }
    }
    $running = Get-Process ollama -ErrorAction SilentlyContinue
    $hasModel = $false
    if ($running) {
        try { $hasModel = ((Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 3).models.name -contains $llm) } catch { }
        if (-not $hasModel -and (Test-Path $models)) {
            Get-Process ollama* -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep 2; $running = $null
        }
    }
    if (-not $running) {
        $set = if (Test-Path $models) { "set `"OLLAMA_MODELS=$models`"&& " } else { "" }
        Launch "cmd /c `"$set`"$ollama`" serve`""
        Write-Host "Ollama started ($llm)" -ForegroundColor Cyan
    }
} else {
    Write-Host "Ollama not installed - AI report will use the rule-based summary." -ForegroundColor Yellow
}

$bind = "127.0.0.1"
if ($Lan) {
    $bind = "0.0.0.0"
    $envFile = Join-Path $root ".env"
    $hasPin = $env:OMNIPHARM_ACCESS_PIN -or ((Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^OMNIPHARM_ACCESS_PIN=.+' -Quiet))
    $usersFile = Join-Path $root "config\users.json"
    $hasUsers = (Test-Path $usersFile) -and (Select-String -Path $usersFile -Pattern '"hash"' -Quiet)
    if (-not $hasPin -and -not $hasUsers) {
        Write-Host "WARNING: LAN mode without login or PIN - anyone on this network can use the server." -ForegroundColor Yellow
        Write-Host "         Create accounts:  .venv\Scripts\python.exe scripts\users.py add <name> --role admin" -ForegroundColor Yellow
        Write-Host "         or add a line OMNIPHARM_ACCESS_PIN=your-pin to .env" -ForegroundColor Yellow
    }
}
New-Item -ItemType Directory -Force (Join-Path $root "logs") | Out-Null
Launch "cmd /c `"`"$py`" -m uvicorn omnipharm.api:app --app-dir src --host $bind --port $Port --no-access-log > logs\api.log 2>&1`""

Write-Host "Loading models (about 10-20 s)..." -NoNewline
$ok = $false
for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep 1
    try { Invoke-RestMethod "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 2 | Out-Null; $ok = $true; break } catch { Write-Host "." -NoNewline }
}
Write-Host ""
if ($ok) {
    Write-Host "OMNI-PHARM ready: $url" -ForegroundColor Green
    if ($Lan) {
        $ips = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127|169\.254)\.' -and $_.PrefixOrigin -ne 'WellKnown' }
        foreach ($ip in $ips) { Write-Host ("LAN:  http://{0}:{1}" -f $ip.IPAddress, $Port) -ForegroundColor Green }
    }
    if (-not $NoBrowser) { Start-Process $url }
} else {
    Write-Host "Server did not start - see logs\api.log" -ForegroundColor Red
    Get-Content (Join-Path $root "logs\api.log") -Tail 15
    exit 1
}
