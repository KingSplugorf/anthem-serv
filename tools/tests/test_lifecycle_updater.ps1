# Portable installer checks with mocked downloads; no network or game needed.
# pwsh -NoProfile -File tools/tests/test_lifecycle_updater.ps1
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$revision = '1111111111111111111111111111111111111111'
$downloadBase = "https://raw.githubusercontent.com/KingSplugorf/anthem-serv/$revision/"
$sandboxRoot = Join-Path ([IO.Path]::GetTempPath()) "anthem-updater-test-$([Guid]::NewGuid().ToString('N'))"
$corruptDownload = $false

function Get-Process { [CmdletBinding()] param([string[]]$Name) }
function Get-CimInstance { [CmdletBinding()] param([string]$ClassName, [string]$Filter) }
function Invoke-WebRequest {
    [CmdletBinding()]
    param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
    if (-not $Uri.StartsWith($downloadBase)) { throw "Unpinned download: $Uri" }
    $relative = $Uri.Substring($downloadBase.Length)
    if ($corruptDownload -and $relative -eq 'tools/mission_lifecycle.py') {
        [IO.File]::WriteAllText($OutFile, 'corrupted download')
    } else {
        Copy-Item -LiteralPath (Join-Path $sourceRoot $relative) -Destination $OutFile
    }
}
function Assert-True($condition, [string]$message) {
    if (-not $condition) { throw $message }
}
function Fixture([string]$name) {
    $root = Join-Path $sandboxRoot $name
    New-Item -ItemType Directory -Force -Path (Join-Path $root 'tools/windows'), (Join-Path $root 'data/certs'),
        (Join-Path $root 'server'), (Join-Path $root 'launcher/node_modules') | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'tools/windows/update-lifecycle.ps1') -Destination (Join-Path $root 'tools/windows/update-test.ps1')
    [IO.File]::WriteAllText((Join-Path $root 'Cargo.toml'), 'original source')
    foreach ($file in @('server/state.json', 'data/certs/test.key', 'game.conf', 'launcher/node_modules/untouched.txt')) {
        [IO.File]::WriteAllText((Join-Path $root $file), 'preserve me')
    }
    return $root
}
try {
    $valid = Fixture 'valid'
    & (Join-Path $valid 'tools/windows/update-test.ps1') -Revision $revision
    $backups = @(Get-ChildItem -LiteralPath $valid -Directory -Filter '.lifecycle-backup-*' -Force)
    Assert-True ($backups.Count -eq 1) 'Expected one source backup'
    Assert-True ((Get-Content -LiteralPath (Join-Path $backups[0].FullName 'Cargo.toml') -Raw) -eq 'original source') 'Original source was not backed up'
    $manifest = Get-Content -LiteralPath (Join-Path $sourceRoot 'tools/windows/lifecycle-files.json') -Raw | ConvertFrom-Json
    foreach ($entry in $manifest.files) {
        Assert-True ((Get-FileHash -LiteralPath (Join-Path $valid $entry.path) -Algorithm SHA256).Hash -ieq $entry.sha256) "Installed hash mismatch: $($entry.path)"
    }
    foreach ($file in @('server/state.json', 'data/certs/test.key', 'game.conf', 'launcher/node_modules/untouched.txt')) {
        Assert-True ((Get-Content -LiteralPath (Join-Path $valid $file) -Raw) -eq 'preserve me') "Modified runtime file: $file"
    }

    $invalid = Fixture 'invalid'
    $corruptDownload = $true
    $rejected = $false
    try { & (Join-Path $invalid 'tools/windows/update-test.ps1') -Revision $revision }
    catch {
        if ($_.Exception.Message -notlike 'Integrity check failed:*') { throw }
        $rejected = $true
    }
    Assert-True $rejected 'Corrupt download was accepted'
    Assert-True ((Get-Content -LiteralPath (Join-Path $invalid 'Cargo.toml') -Raw) -eq 'original source') 'Source changed before verification finished'
    Assert-True (@(Get-ChildItem -LiteralPath $invalid -Directory -Filter '.lifecycle-backup-*' -Force).Count -eq 0) 'Backup/install began before all hashes passed'
    Write-Host "Updater passed: $($manifest.files.Count) verified sources, original-source backup, preserved runtime data, corrupt-download rejection"
}
finally {
    Remove-Item -LiteralPath $sandboxRoot -Recurse -Force -ErrorAction SilentlyContinue
}
