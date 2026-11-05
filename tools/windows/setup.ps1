# Developer setup for native Windows x64 (not a distributable installer).
# Run from PowerShell: .\tools\windows\setup.ps1
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
foreach ($command in @('rustup.exe', 'cargo.exe', 'node.exe', 'npm.cmd', 'py.exe', 'openssl.exe')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Missing $command. See tools/windows/README.md for prerequisites."
    }
}
function Check-Exit([string] $operation) {
    if ($LASTEXITCODE -ne 0) { throw "$operation failed (exit $LASTEXITCODE)" }
}
Push-Location $root
try {
    py.exe -3 -X utf8 -I -c "import struct, sys; assert struct.calcsize('P') == 8 and sys.version_info >= (3,11), '64-bit Python 3.11+ is required'"
    Check-Exit 'Python architecture check'
    py.exe -3 -m venv .venv-windows
    Check-Exit 'Python virtual environment creation'
    & .\.venv-windows\Scripts\python.exe -m pip install -r tools/windows/requirements.txt
    Check-Exit 'Python dependency installation'
    rustup.exe toolchain install 1.90.0 --profile minimal
    Check-Exit 'Rust toolchain installation'
    cargo.exe +1.90.0 build --locked --workspace --bins
    Check-Exit 'Rust build (MSVC C++ build tools must be installed)'
    $cert = Join-Path $root 'data\certs\test.crt'
    $key = Join-Path $root 'data\certs\test.key'
    if ((Test-Path -LiteralPath $cert) -xor (Test-Path -LiteralPath $key)) {
        throw 'Incomplete certificate pair: preserve your files and repair data/certs before continuing.'
    }
    if (-not (Test-Path -LiteralPath $cert)) {
        New-Item -ItemType Directory -Force (Split-Path $cert -Parent) | Out-Null
        openssl.exe req -x509 -newkey rsa:2048 -nodes -days 30 -keyout $key -out $cert -subj '/CN=spring18.gosredirector.ea.com' -addext 'subjectAltName=DNS:spring18.gosredirector.ea.com'
        Check-Exit 'Development certificate generation'
    }
    openssl.exe x509 -in $cert -checkend 0 -noout
    Check-Exit 'Certificate expiry check'
    Push-Location launcher
    try {
        npm.cmd ci --no-audit --no-fund
        Check-Exit 'Launcher installation'
        npm.cmd test
        Check-Exit 'Launcher tests'
    } finally { Pop-Location }
    Write-Host 'Development setup complete. From launcher, run npm start. Windows gameplay is experimental.'
} finally { Pop-Location }
