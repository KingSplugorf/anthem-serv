[CmdletBinding()]
param(
    [ValidateSet('Development', 'Fresh', 'Creator', 'Tutorial')][string]$Profile = 'Development',
    [ValidateLength(1, 64)][string]$PilotName = 'Freelancer',
    [ValidateSet('Male', 'Female')][string]$Gender = 'Male',
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$')][string]$ProfileId = 'pilot-1',
    [switch]$LoadLocalCa
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if ([string]::IsNullOrWhiteSpace($PilotName) -or $PilotName -match '[\x00-\x1f\x7f]') {
    throw 'PilotName must contain a name without control characters.'
}

# Keep using the launcher for certificate handling, hosts, patches and cleanup.
# Rebuild the services: Blaze and the native lifecycle hooks must agree.
Push-Location $repoRoot
$previous = @{}
try {
    & cargo.exe '+1.90.0' build --locked --workspace --bins
    if ($LASTEXITCODE -ne 0) { throw 'Service build failed; the game was not started.' }

    $built = Join-Path $repoRoot 'target\debug\blaze.exe'
    if (-not (Test-Path -LiteralPath $built)) { throw "Build output not found: $built" }
    # The launcher prefers a complete bin/ or release/ set over debug/. Preserve
    # all three old executables before installing this build in the selected set.
    foreach ($directory in @('bin', 'target\release')) {
        $complete = $true
        foreach ($service in @('redirector', 'blaze', 'bigs')) {
            if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "$directory\$service.exe"))) { $complete = $false }
        }
        if ($complete) {
            foreach ($service in @('redirector', 'blaze', 'bigs')) {
                $destination = Join-Path $repoRoot "$directory\$service.exe"
                $backup = "$destination.before-profile-$([Guid]::NewGuid().ToString('N'))"
                Copy-Item -LiteralPath $destination -Destination $backup
                Copy-Item -LiteralPath (Join-Path $repoRoot "target\debug\$service.exe") -Destination $destination -Force
                Write-Host "Previous $service preserved at $backup"
            }
            break
        }
    }

    $values = @{
        ANTHEM_PROFILE = $Profile.ToLowerInvariant()
        ANTHEM_PROFILE_ID = $ProfileId
        ANTHEM_PILOT_NAME = $PilotName.Trim()
        ANTHEM_PILOT_GENDER = $Gender
        ANTHEM_TLS_DIAGNOSTICS = $null
        ANTHEM_SKIP_PATCHES = $null
    }
    if ($LoadLocalCa) { $values['ANTHEM_LOAD_LOCAL_CA'] = '1' }
    foreach ($key in $values.Keys) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $values[$key], 'Process')
    }
    Write-Host "Profile: $Profile; slot: $ProfileId. Use the launcher save selector to switch characters."
    & npm.cmd --prefix (Join-Path $repoRoot 'launcher') start
    if ($LASTEXITCODE -ne 0) { throw "Launcher exited with code $LASTEXITCODE" }
}
finally {
    foreach ($key in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
    }
    Pop-Location
}
