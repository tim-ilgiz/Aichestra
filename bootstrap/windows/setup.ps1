# Thin Windows bootstrap wrapper — portable logic lives in Python.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$env:PYTHONPATH = if ($env:PYTHONPATH) {
    "$Root\src;$env:PYTHONPATH"
} else {
    "$Root\src"
}
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Error "Python 3.11+ is required on PATH (python or python3)."
}
& $python.Source -m aichestra bootstrap @args
exit $LASTEXITCODE
