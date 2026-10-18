# Copy a RUNNABLE OMNI-PHARM to a USB stick / other folder so someone else can review it on their own Windows PC.
# (Keep this file ASCII-only: Windows PowerShell 5.1 reads a .ps1 without BOM in the ANSI code page.)
#
#   powershell -ExecutionPolicy Bypass -File scripts\make_review_package.ps1 -Target E:\OMNI-PHARM
#   -WithAI    also copy the local LLM (AI chat, prescription photo): +10 GB, and one 6.9 GB file -> the USB must be
#              exFAT or NTFS (FAT32 cannot hold a file over 4 GB)
#
# What is copied (about 6.5 GB without AI): code, built UI, data/processed, models (artifacts), the Python
# environment (.venv + runtime\python), docs, validation reports, the reviewer's guide and feedback form
# (docs\review_package, made by scripts\review_feedback_form.py) at the top of the folder.
# What is NOT copied:
#   data\raw, data\interim (40 GB of downloads, only needed to rebuild the data - scripts\pipeline.py)
#   .env (API key), config\users.json (password hashes), .git and runtime\git, runtime\node (the UI is prebuilt)
#   pharmacists' names: data\review, the returned review workbooks under docs\validation\*\, logs\validation\review_*,
#   logs\audit
# Nothing in the source folder is changed. The reviewer double-clicks the start .bat - no install, no internet.
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
    $a = @($src, (Join-Path $Target $rel), "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/MT:8")
    if ($xd) { $a += "/XD"; $a += $xd }
    if ($xf) { $a += "/XF"; $a += $xf }
    & robocopy @a | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copy failed: $rel (robocopy exit $LASTEXITCODE)" }
    Write-Host "  copied $rel"
}

Write-Host "Copying to $Target ..." -ForegroundColor Cyan
$junk = @("__pycache__", ".pytest_cache", ".ruff_cache")
Mirror "src" $junk
Mirror "scripts" $junk
Mirror "tests" $junk
Mirror "docs"
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
# returned review workbooks (pharmacists' names) live in subfolders of docs\validation
Get-ChildItem (Join-Path $Target "docs\validation") -Directory -ErrorAction SilentlyContinue |
    ForEach-Object { Get-ChildItem $_.FullName -Filter *.xlsx | Remove-Item -Force }
# top-level launchers and documents
foreach ($f in Get-ChildItem $root -File -Force) {
    if ($f.Name -eq ".env") { continue }
    # the developer's launchers (backup needs git; this copier is not for the reviewer)
    if ($f.Extension -eq ".bat" -and (Select-String -Path $f.FullName -Pattern "backup\.ps1|make_review_package\.ps1" -Quiet)) { continue }
    if ($f.Extension -in @(".bat", ".ps1", ".md", ".toml", ".txt") -or $f.Name -eq ".env.example") {
        Copy-Item $f.FullName (Join-Path $Target $f.Name) -Force
    }
}
# the reviewer's guide and feedback form at the top of the folder
Get-ChildItem (Join-Path $root "docs\review_package") -File | ForEach-Object { Copy-Item $_.FullName $Target -Force }

# safety: no secrets / names in the copy
$leaks = @(".env", "config\users.json", "data\review", "logs\audit", ".git") | Where-Object { Test-Path (Join-Path $Target $_) }
if ($leaks) { $leaks | ForEach-Object { Remove-Item -Recurse -Force (Join-Path $Target $_) }; Write-Host "Removed from the copy: $leaks" -ForegroundColor Yellow }

$size = SizeGB @($Target)
$kind = if ($WithAI) { "with AI chat" } else { "without AI chat (fixed-text answers)" }
Write-Host ("Done: {0} ({1} GB, {2})" -f $Target, $size, $kind) -ForegroundColor Green
Write-Host "On the reviewer's PC: copy the folder to the PC, then follow the guide text file at the top of the folder."
exit 0
