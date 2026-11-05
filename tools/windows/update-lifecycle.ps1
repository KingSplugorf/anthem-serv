[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-fA-F0-9]{40}$')][string]$Revision,
    [ValidateSet('Development', 'Fresh', 'Creator', 'Tutorial')][string]$Profile = 'Development',
    [switch]$Start,
    [switch]$LoadLocalCa
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'Cargo.toml'))) {
    throw 'Place this updater inside tools\windows of your existing anthem-serv folder.'
}
if (@(Get-Process -Name Anthem, dylan.main_win64_retail -ErrorAction SilentlyContinue).Count) {
    throw 'Close the game and launcher before installing the lifecycle update.'
}
$launchers = @(Get-CimInstance Win32_Process -Filter "Name = 'electron.exe'" -ErrorAction SilentlyContinue |
    Where-Object { ([string]$_.CommandLine).IndexOf($repoRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
if ($launchers.Count) { throw 'Close the Anthem launcher before installing this update.' }

$base = "https://raw.githubusercontent.com/KingSplugorf/anthem-serv/$Revision"
$stage = Join-Path ([IO.Path]::GetTempPath()) "anthem-lifecycle-$([Guid]::NewGuid().ToString('N'))"
$backup = Join-Path $repoRoot ".lifecycle-backup-$(Get-Date -Format 'yyyyMMdd-HHmmssfff')-$([Guid]::NewGuid().ToString('N').Substring(0,8))"
New-Item -ItemType Directory -Path $stage -Force | Out-Null

try {
    $manifestPath = Join-Path $stage 'manifest.json'
    Invoke-WebRequest -UseBasicParsing -ErrorAction Stop -Uri "$base/tools/windows/lifecycle-files.json" -OutFile $manifestPath
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $files = @($manifest.files)
    if ($manifest.schema -ne 1 -or $files.Count -lt 1 -or $files.Count -gt 200) { throw 'Invalid lifecycle update manifest.' }
    $seen = @{}
    foreach ($entry in $files) {
        $relative = [string]$entry.path
        if ($relative -notmatch '^[a-zA-Z0-9_./-]+$' -or $relative.StartsWith('/') -or
            @($relative.Split('/') | Where-Object { $_ -eq '..' -or $_ -eq '.' }).Count -or
            ($relative -notin @('Cargo.toml', 'Cargo.lock', '.gitignore') -and $relative -notmatch '^(crates|launcher|tools)/') -or
            ([string]$entry.sha256) -notmatch '^[a-fA-F0-9]{64}$' -or $seen.ContainsKey($relative.ToLowerInvariant())) {
            throw "Unsafe or invalid manifest entry: $relative"
        }
        $seen[$relative.ToLowerInvariant()] = $true
        $destination = Join-Path $stage $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Invoke-WebRequest -UseBasicParsing -ErrorAction Stop -Uri "$base/$relative" -OutFile $destination
        if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ine [string]$entry.sha256) {
            throw "Integrity check failed: $relative. Existing source files were not changed."
        }
    }

    # All downloads are verified before source replacement. Runtime saves,
    # certificates, game.conf, the game installation and node_modules are excluded.
    foreach ($entry in $files) {
        $original = Join-Path $repoRoot $entry.path
        if (Test-Path -LiteralPath $original) {
            $saved = Join-Path $backup $entry.path
            New-Item -ItemType Directory -Path (Split-Path -Parent $saved) -Force | Out-Null
            Copy-Item -LiteralPath $original -Destination $saved
        }
    }
    Write-Host "Original source files preserved in $backup"
    foreach ($entry in $files) {
        $destination = Join-Path $repoRoot $entry.path
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $stage $entry.path) -Destination $destination -Force
    }
    Write-Host "Lifecycle source update installed from $Revision"
    if ($Start) {
        & (Join-Path $repoRoot 'tools\windows\play-profile.ps1') -Profile $Profile -LoadLocalCa:$LoadLocalCa
    } else {
        Write-Host 'Rebuild the services before playing: .\tools\windows\play-profile.ps1 -Profile Development -LoadLocalCa'
    }
}
finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
