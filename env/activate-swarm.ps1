# Dot-source this in PowerShell before any ratchet command:   . .\env\activate-swarm.ps1
# Same as activate-swarm.sh: project Python, UTF-8 output, state kept inside the repo, keys from env/secrets.env.
$root = Split-Path -Parent $PSScriptRoot
$env:JIUWENSWARM_HOME = $root
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$secrets = Join-Path $root "env\secrets.env"
if (Test-Path $secrets) {
    foreach ($line in Get-Content $secrets) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            Set-Item -Path "env:$($matches[1])" -Value $matches[2].Trim().Trim('"').Trim("'")
        }
    }
}
. (Join-Path $root ".venv-swarm\Scripts\Activate.ps1")
Write-Host "swarm env ready. python = $((Get-Command python).Source)  MODEL_NAME = $env:MODEL_NAME"
