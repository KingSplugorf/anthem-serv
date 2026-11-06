param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Shield','Hud','Pickup')][string]$Kind
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $root
$games = @(Get-Process -Name Anthem -ErrorAction SilentlyContinue)
if ($games.Count -ne 1) { throw 'Keep exactly one Anthem game running inside a mission.' }
switch ($Kind) {
    'Shield' { Write-Host 'Shoot ONE nearby enemy with a blue shield using ordinary bullets. Avoid abilities and explosive rounds. Note its type, gun, and body part.' }
    'Hud' { Write-Host 'Avoid pickups. Take one environmental hit, then wait. Note whether health/shield bars appear. Do not use a heal command.' }
    'Pickup' { Write-Host 'Begin injured beside a health pickup, away from attackers. Wait 5 seconds, collect ONE pickup, then wait. Note the collection time.' }
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$output = Join-Path $root "combat-$Kind-$($games[0].Id)-$stamp.json"
& py -3 (Join-Path $PSScriptRoot 'inspect_combat.py') --pid $games[0].Id --watch-seconds 30 --output $output
if ($LASTEXITCODE -ne 0) { throw 'Recording failed; see the Python error above.' }
Write-Host "Share $output and your observations. Recording did not change health, shields, damage or saves."
