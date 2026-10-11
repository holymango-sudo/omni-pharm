# OMNI-PHARM installer for a new Windows PC (Python 3.11 + Node.js 20+ required; NVIDIA GPU optional).
# Usage:  powershell -ExecutionPolicy Bypass -File .\install.ps1            (CUDA GPU build of PyTorch)
#         powershell -ExecutionPolicy Bypass -File .\install.ps1 -Cpu       (no NVIDIA GPU)
# Data and trained models are NOT downloaded by this script: copy the `data\processed`, `data\cache` and
# `artifacts\v2` folders from an existing installation, or rebuild them (README, "Data pipeline").
param([switch]$Cpu)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root
# keep every download cache inside this folder (pip, npm) - nothing is written to C:
$env:PIP_CACHE_DIR = Join-Path $root "runtime\cache\pip"
$env:npm_config_cache = Join-Path $root "runtime\cache\npm"

$bundled = Join-Path $root "runtime\python\python.exe"  # self-contained copy shipped in .\runtime
$py = (Get-Command py -ErrorAction SilentlyContinue)
if (Test-Path $bundled) { $pyexe = $bundled; $pyargs = @() }
elseif ($py) { $pyexe = "py"; $pyargs = @("-3.11") } else { $pyexe = "python"; $pyargs = @() }
if (Test-Path (Join-Path $root "runtime\node\node.exe")) { $env:PATH = (Join-Path $root "runtime\node") + ";" + $env:PATH }
& $pyexe @pyargs --version
if (-not (Test-Path ".venv")) { & $pyexe @pyargs -m venv .venv }
$vpy = Join-Path $root ".venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip

# PyTorch first, from the right index, so the lock file never pulls a CPU build over a GPU one
$torch = (Select-String -Path requirements-lock.txt -Pattern '^torch==(.+)$').Matches[0].Groups[1].Value
if ($Cpu) {
    & $vpy -m pip install ("torch==" + ($torch -replace '\+.*$', '')) --index-url https://download.pytorch.org/whl/cpu
} else {
    $cu = ($torch -split '\+')[1]
    & $vpy -m pip install "torch==$torch" --index-url "https://download.pytorch.org/whl/$cu"
}
$tmp = Join-Path $root "runtime\tmp"; New-Item -ItemType Directory -Force $tmp | Out-Null
Get-Content requirements-lock.txt | Where-Object { $_ -notmatch '^torch==' } | Set-Content -Encoding ascii "$tmp\omnipharm-req.txt"
& $vpy -m pip install -r "$tmp\omnipharm-req.txt"
& $vpy -m pip install -e . --no-deps
# relative source path, so the folder keeps working when the drive letter / PC changes
Get-ChildItem (Join-Path $root ".venv\Lib\site-packages\__editable__.omnipharm*.pth") | ForEach-Object {
    Set-Content $_.FullName -Encoding ascii -Value "import os, sys; p = os.path.join(os.path.dirname(sys.prefix), 'src'); p in sys.path or sys.path.append(p)"
}

Push-Location frontend
npm ci --no-audit --no-fund
npm run build
Pop-Location

& $vpy scripts\verify_env.py
Write-Host ""
$oll = Join-Path $root "runtime\ollama\ollama.exe"
if ((Test-Path $oll) -and -not (Test-Path (Join-Path $root "runtime\ollama-models\manifests"))) {
    Write-Host "AI report model: run   `$env:OLLAMA_MODELS='$root\runtime\ollama-models'; & '$oll' pull gemma4:12b" -ForegroundColor Yellow
}
Write-Host "Install finished. Python / Ollama / Node / AI model are kept in .\runtime (portable with this folder)." -ForegroundColor Green
Write-Host "Start with the 'OMNI-PHARM' launcher .bat in this folder (or start_servers.ps1)" -ForegroundColor Green
