[CmdletBinding()]
param(
    [ValidateLength(1, 64)][string]$PilotName = 'Freelancer',
    [ValidateSet('Male', 'Female')][string]$Gender = 'Male',
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$')][string]$ProfileId = 'pilot-1',
    [switch]$LoadLocalCa
)

# Compatibility entry point for pre-created fresh-profile commands.
# The launcher also offers Original save and New character.
& (Join-Path $PSScriptRoot 'play-profile.ps1') -Profile Fresh @PSBoundParameters
