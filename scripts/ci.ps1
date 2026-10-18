# OMNI-PHARM checks - the same steps for a person, the git pre-commit hook and a release.
#
#   powershell -ExecutionPolicy Bypass -File scripts\ci.ps1            quick: lint + rule files + fast tests (the hook)
#   powershell -ExecutionPolicy Bypass -File scripts\ci.ps1 full       + every test, the 4 clinical audits, the behaviour
#                                                                        snapshot, a random sweep, the frontend build
#   -NoFrontend   skip the frontend build (full)
#
# Exit code 0 only when every step passed. 'full' needs the built data and models (data/processed, artifacts).
# The behaviour snapshot fails whenever results change: if the change is intended, look at the listed differences and
# record the new behaviour with  .venv\Scripts\python.exe scripts\snapshot_equivalence.py save  (docs/CONTRIBUTING.md).
param([ValidateSet("quick", "full")][string]$Mode = "quick", [switch]$NoFrontend)
$ErrorActionPreference = "Continue"  # native tools write progress to stderr; failures are checked via exit codes
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"
$env:OMNIPHARM_ONLINE_LOOKUP = "0"  # no network during checks
$script:failed = @()
$t0 = Get-Date

function Step([string]$name, [scriptblock]$cmd) {
    Write-Host "== $name" -ForegroundColor Cyan
    $t = Get-Date
    & $cmd
    if ($LASTEXITCODE -ne 0) {
        $script:failed += $name
        Write-Host "   FAILED: $name" -ForegroundColor Red
    } else {
        Write-Host ("   ok ({0:n0} s)" -f ((Get-Date) - $t).TotalSeconds) -ForegroundColor Green
    }
}

Push-Location $root
try {
    Step "ruff (lint)" { & $py -m ruff check src scripts tests }
    Step "rule files (sources, levels, review requests)" { & $py -m pytest tests/test_rules.py -q -p no:cacheprovider }
    if ($Mode -eq "quick") {
        Step "fast tests" { & $py -m pytest tests -q -p no:cacheprovider -m "not slow" }
    } else {
        # the tests' own engine on the CPU: with a second engine on the GPU next to the running server, Ollama could not
        # load the vision model (llama.cpp 'error loading model: vector' while fitting memory) - and results stay the
        # same on any machine (GitHub's runners have no GPU either)
        Step "all tests (engine on CPU)" {
            $env:CUDA_VISIBLE_DEVICES = "-1"
            try { & $py -m pytest tests -q -p no:cacheprovider } finally { Remove-Item Env:CUDA_VISIBLE_DEVICES }
        }
        Step "audit: development set" { & $py scripts/audit_combos.py }
        # measurement only: never change rules to fit the holdout set
        Step "audit: holdout set (measurement only)" { & $py scripts/audit_combos.py --holdout }
        Step "audit: patient cases" { & $py scripts/audit_combos.py --patients }
        Step "audit: food cases" { & $py scripts/audit_combos.py --foods }
        Step "behaviour snapshot (996 analyses)" { & $py scripts/snapshot_equivalence.py compare }
        Step "random sweep (500)" { & $py scripts/sweep_random.py 500 }
        if (-not $NoFrontend) {
            Step "frontend build" {
                $env:PATH = (Join-Path $root "runtime\node") + ";" + $env:PATH
                Push-Location (Join-Path $root "frontend")
                try { & (Join-Path $root "runtime\node\npm.cmd") run build --silent } finally { Pop-Location }
            }
        }
    }
} finally { Pop-Location }

$secs = [math]::Round(((Get-Date) - $t0).TotalSeconds)
if ($script:failed.Count) {
    Write-Host ("FAILED ($Mode, $secs s): " + ($script:failed -join ", ")) -ForegroundColor Red
    exit 1
}
Write-Host "All $Mode checks passed ($secs s)." -ForegroundColor Green
exit 0
