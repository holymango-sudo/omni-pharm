# Stops the OMNI-PHARM API (port 8000 by default). Ollama is left running (other apps may use it);
# add -Ollama to stop it too.
param([int]$Port = 8000, [switch]$Ollama)
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
if ($Ollama) { Get-Process ollama* -ErrorAction SilentlyContinue | Stop-Process -Force }
Write-Host "Stopped."
