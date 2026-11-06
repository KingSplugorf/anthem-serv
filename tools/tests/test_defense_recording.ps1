$ErrorActionPreference = 'Stop'
$global:anthemDefenseTestInvocations = @()
function Get-Process { [CmdletBinding()] param([string]$Name) return [pscustomobject]@{ Id = 123 } }
function py { $global:anthemDefenseTestInvocations += ,@($args); $global:LASTEXITCODE = 0 }
$originalLocation = Get-Location
try {
    $recorder = Join-Path (Split-Path -Parent $PSScriptRoot) 'windows/record-defense.ps1'
    foreach ($kind in @('Shield','Hud','Pickup')) {
        & $recorder -Kind $kind
        $call = $global:anthemDefenseTestInvocations[-1]
        if ($call[0] -ne '-3' -or $call[2] -ne '--pid' -or $call[3] -ne 123 -or
            $call[4] -ne '--watch-seconds' -or $call[5] -ne 30 -or
            $call[6] -ne '--output' -or $call[7] -notmatch "combat-$kind-123-.*\.json$") {
            throw "Incorrect isolated $kind recording arguments"
        }
    }
    if ($global:anthemDefenseTestInvocations.Count -ne 3) { throw 'Unexpected recording count' }
    Write-Host 'Three separate read-only defense recording commands passed.'
} finally { Set-Location -LiteralPath $originalLocation; Remove-Variable anthemDefenseTestInvocations -Scope Global -ErrorAction SilentlyContinue }
