# OMNI-PHARM backup: version history (git) + a portable copy of the code, optionally the built data / models.
#
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1                  code: commit + git bundle
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Full            + processed data, models, settings
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Full -Target E:\OMNI-PHARM_backups   (other drive)
#   -Message "what changed"   commit message        -Keep 30   number of code bundles to keep        -Quiet
#
# Restore the code anywhere:   runtime\git\cmd\git.exe clone <backup>\code\omnipharm_YYYYMMDD_HHmm.bundle OMNI-PHARM
# Restore data (Full):         copy <backup>\full\data\processed, artifacts, config, .env back into the folder
#
# A backup on the same SSD protects against mistakes, not against losing the SSD: use -Target on another drive / USB.
#   -IfChanged   only when the code changed (used by start_servers.ps1 for an automatic snapshot at every start)
param([string]$Target = "", [switch]$Full, [string]$Message = "", [int]$Keep = 30, [switch]$Quiet, [switch]$IfChanged)
$ErrorActionPreference = "Continue"  # git writes progress to stderr; failures are checked via exit codes
$root = Split-Path $PSScriptRoot -Parent
$git = Join-Path $root "runtime\git\cmd\git.exe"
if (-not (Test-Path $git)) { Write-Host "git not found at $git" -ForegroundColor Red; exit 1 }
if (-not $Target) { $Target = Join-Path (Split-Path $root -Parent) "OMNI-PHARM_backups" }
$stamp = Get-Date -Format "yyyyMMdd_HHmm"
function Say($m, $c = "Gray") { if (-not $Quiet) { Write-Host $m -ForegroundColor $c } }

# 1. version history: commit whatever changed since the last snapshot
Push-Location $root
try {
    & $git add -A 2>$null
    $pending = & $git status --porcelain
    if ($pending) {
        if (-not $Message) { $Message = "Snapshot " + (Get-Date -Format "yyyy-MM-dd HH:mm") }
        $msgFile = Join-Path $env:TEMP "omnipharm_commit_$stamp.txt"
        [System.IO.File]::WriteAllText($msgFile, $Message + "`n", (New-Object System.Text.UTF8Encoding($false)))
        $env:OMNIPHARM_SNAPSHOT = "1"  # the pre-commit checks (install_hooks.ps1) never block a backup
        & $git commit -q -F $msgFile
        Remove-Item Env:OMNIPHARM_SNAPSHOT -ErrorAction SilentlyContinue
        Remove-Item $msgFile -ErrorAction SilentlyContinue
        Say ("Committed " + @($pending).Count + " changed file(s): " + $Message) "Cyan"
    } else {
        Say "No code changes since the last snapshot."
        if ($IfChanged -and -not $Full) { exit 0 }
    }

    # 2. portable copy of the whole history (one file, restorable with git clone)
    $codeDir = Join-Path $Target "code"
    New-Item -ItemType Directory -Force $codeDir | Out-Null
    $bundle = Join-Path $codeDir "omnipharm_$stamp.bundle"
    & $git bundle create $bundle --all 2>$null
    & $git bundle verify $bundle 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "bundle verification failed: $bundle" }
    $head = (& $git log -1 --format="%h %s")
    Say "Code bundle: $bundle  ($([math]::Round((Get-Item $bundle).Length / 1KB)) KB, HEAD $head)" "Green"
    Get-ChildItem $codeDir -Filter "omnipharm_*.bundle" | Sort-Object Name -Descending | Select-Object -Skip $Keep |
        Remove-Item -Force
} finally { Pop-Location }

# 3. optional: everything needed to run without rebuilding (hours of downloads / training)
if ($Full) {
    $fullDir = Join-Path $Target "full"
    foreach ($d in @("data\processed", "data\cache", "data\review", "artifacts", "config", "docs\validation")) {
        $src = Join-Path $root $d
        if (Test-Path $src) {
            robocopy $src (Join-Path $fullDir $d) /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "copy failed: $d" }
        }
    }
    $envFile = Join-Path $root ".env"
    if (Test-Path $envFile) { Copy-Item $envFile (Join-Path $fullDir ".env") -Force }  # API key: keep this folder private
    $size = (Get-ChildItem $fullDir -Recurse -File | Measure-Object Length -Sum).Sum / 1GB
    Say ("Data / models / settings mirrored to $fullDir (" + [math]::Round($size, 2) + " GB). Contains .env (API key) - keep it private.") "Green"
}
Add-Content -Path (Join-Path $Target "backup.log") -Value ((Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "  " + $(if ($Full) { "full" } else { "code" }) + "  $stamp") -Encoding UTF8
Say "Backup folder: $Target" "Green"
