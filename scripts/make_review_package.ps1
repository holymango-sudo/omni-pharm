# Copy a RUNNABLE OMNI-PHARM to a USB stick / other folder so someone else can review it on their own Windows PC.
#
#   powershell -ExecutionPolicy Bypass -File scripts\make_review_package.ps1 -Target E:\OMNI-PHARM
#   -WithAI    also copy the local LLM (AI chat, prescription photo): +10 GB, and one 6.9 GB file -> the USB must be
#              exFAT or NTFS (FAT32 cannot hold a file over 4 GB)
#
# What is copied (about 6.5 GB without AI): code, built UI, data/processed, models (artifacts), the Python
# environment (.venv + runtime\python), docs, validation reports, the reviewer guide and feedback form.
# What is NOT copied:
#   data\raw, data\interim (40 GB of downloads, only for rebuilding the data - scripts\pipeline.py)
#   .env (API key), config\users.json (password hashes), .git and runtime\git, runtime\node (UI is prebuilt)
#   pharmacists' names: data\review, docs\validation\검수결과\*.xlsx, logs\validation\review_*, logs\audit
# Nothing on the source is changed. The reviewer double-clicks "OMNI-PHARM 실행.bat" - no install, no internet.
param([Parameter(Mandatory = $true)][string]$Target, [switch]$WithAI)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$Target = [System.IO.Path]::GetFullPath($Target)
if ($Target.TrimEnd('\') -eq $root.TrimEnd('\') -or $Target.StartsWith($root + '\')) {
    Write-Host "Target must be outside the program folder: $Target" -ForegroundColor Red; exit 1
}

function SizeGB($paths) {
    $s = 0
    foreach ($p in $paths) { if (Test-Path $p) { $s += (Get-ChildItem $p -Recurse -File -Force | Measure-Object Length -Sum).Sum } }
    return [math]::Round($s / 1GB, 2)
}

# ---- room and file system on the target drive
$qualifier = Split-Path $Target -Qualifier
$vol = Get-Volume -DriveLetter $qualifier.TrimEnd(':') -ErrorAction SilentlyContinue
$aiDirs = @("runtime\ollama", "runtime\ollama-models") | ForEach-Object { Join-Path $root $_ }
$need = 6.6 + $(if ($WithAI) { SizeGB $aiDirs } else { 0 })
if ($vol) {
    $free = [math]::Round($vol.SizeRemaining / 1GB, 1)
    Write-Host ("Target {0} ({1}): {2} GB free, about {3} GB needed" -f $qualifier, $vol.FileSystemType, $free, $need)
    if ($free -lt $need) { Write-Host "Not enough space on $qualifier." -ForegroundColor Red; exit 1 }
    if ($WithAI -and $vol.FileSystemType -eq "FAT32") {
        Write-Host "FAT32 cannot hold the 6.9 GB AI model file. Format the USB as exFAT, or run without -WithAI." -ForegroundColor Red
        exit 1
    }
}

New-Item -ItemType Directory -Force $Target | Out-Null
function Mirror([string]$rel, [string[]]$xd = @(), [string[]]$xf = @()) {
    $src = Join-Path $root $rel
    if (-not (Test-Path $src)) { return }
    $args = @($src, (Join-Path $Target $rel), "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/MT:8")
    if ($xd) { $args += "/XD"; $args += $xd }
    if ($xf) { $args += "/XF"; $args += $xf }
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copy failed: $rel (robocopy $LASTEXITCODE)" }
    Write-Host "  copied $rel"
}

Write-Host "Copying to $Target ..." -ForegroundColor Cyan
$junk = @("__pycache__", ".pytest_cache", ".ruff_cache")
Mirror "src" $junk
Mirror "scripts" $junk
Mirror "tests" $junk
Mirror "docs" @() @("*.xlsx.bak")
Mirror "frontend" (@("node_modules") + $junk)
Mirror "artifacts" $junk
Mirror "data\processed"
Mirror "data\cache"
Mirror "config" @() @("users.json")
Mirror ".venv" $junk
Mirror "runtime\python" $junk
Mirror "logs\validation" @() @("review_*")
if ($WithAI) {
    Mirror "runtime\ollama"
    Mirror "runtime\ollama-models"
}
# pharmacists' answer files carry their names
Get-ChildItem (Join-Path $Target "docs\validation\검수결과") -Filter *.xlsx -ErrorAction SilentlyContinue | Remove-Item -Force
foreach ($f in Get-ChildItem $root -File -Force) {
    if ($f.Name -in @(".env", ".git")) { continue }
    if ($f.Extension -in @(".bat", ".ps1", ".md", ".toml", ".txt", ".example") -or $f.Name -like ".env.example") {
        Copy-Item $f.FullName (Join-Path $Target $f.Name) -Force
    }
}
# the reviewer's guide and feedback form at the top of the folder
$guide = Join-Path $root "docs\review_package\README_FIRST.txt"
Copy-Item $guide (Join-Path $Target "0_START_HERE.txt") -Force
$form = Join-Path $root "docs\validation\프로그램_검수_의견서.xlsx"
if (Test-Path $form) { Copy-Item $form (Join-Path $Target (Split-Path $form -Leaf)) -Force }

# safety: no secrets / names in the copy
$leaks = @(".env", "config\users.json", "data\review") | Where-Object { Test-Path (Join-Path $Target $_) }
if ($leaks) { Write-Host "Removed from the copy: $leaks" -ForegroundColor Yellow; $leaks | ForEach-Object { Remove-Item -Recurse -Force (Join-Path $Target $_) } }

$size = SizeGB @($Target)
Write-Host ("Done: {0} ({1} GB){2}" -f $Target, $size, $(if ($WithAI) { " - with AI chat" } else { " - without AI chat (rule-based summaries)" })) -ForegroundColor Green
Write-Host "On the reviewer's PC: copy the folder to the PC (faster than running from USB), then double-click 'OMNI-PHARM 실행.bat'."
