$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& "$projectRoot\.venv\Scripts\python.exe" "$PSScriptRoot\infra_runtime.py" start
exit $LASTEXITCODE
