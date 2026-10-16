# Install the git pre-commit hook: every commit runs scripts\ci.ps1 quick (lint + rule files + fast tests, about a
# minute) and is refused when a check fails.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_hooks.ps1           install
#   powershell -ExecutionPolicy Bypass -File scripts\install_hooks.ps1 -Remove   uninstall
#
# Automatic snapshots (scripts\backup.ps1, run by start_servers.ps1) set OMNIPHARM_SNAPSHOT=1 and are not checked:
# a backup must never be blocked by a failing test.
param([switch]$Remove)
$root = Split-Path $PSScriptRoot -Parent
$hook = Join-Path $root ".git\hooks\pre-commit"
if ($Remove) {
    if (Test-Path $hook) { Remove-Item $hook -Force; Write-Host "pre-commit hook removed." }
    exit 0
}
$body = @'
#!/bin/sh
# OMNI-PHARM pre-commit (installed by scripts/install_hooks.ps1): lint + rule files + fast tests.
# Automatic snapshots (scripts/backup.ps1) set OMNIPHARM_SNAPSHOT=1: a backup is never blocked by a failing check.
if [ "$OMNIPHARM_SNAPSHOT" = "1" ]; then exit 0; fi
exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/ci.ps1 quick
'@
# LF line endings, no BOM: the hook is run by git's sh
[System.IO.File]::WriteAllText($hook, $body.Replace("`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "pre-commit hook installed: $hook" -ForegroundColor Green
Write-Host "Each commit now runs scripts\ci.ps1 quick. Before a release run: scripts\ci.ps1 full"
