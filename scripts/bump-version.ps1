<#
.SYNOPSIS
  Bump extension/manifest.json semver (patch | minor | major).

.EXAMPLE
  .\scripts\bump-version.ps1
  .\scripts\bump-version.ps1 patch
  .\scripts\bump-version.ps1 minor
  .\scripts\bump-version.ps1 major
#>

[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet("patch", "minor", "major")]
  [string]$Level = "patch"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $repoRoot "extension\manifest.json"

if (-not (Test-Path -LiteralPath $manifestPath)) {
  throw "manifest not found: $manifestPath"
}

$raw = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8

if ($raw -notmatch '"version"\s*:\s*"(?<ver>\d+\.\d+\.\d+)"') {
  throw "Could not find a semver version field in manifest.json"
}

$oldVersion = $Matches["ver"]
$parts = $oldVersion.Split(".") | ForEach-Object { [int]$_ }
$major = $parts[0]
$minor = $parts[1]
$patch = $parts[2]

switch ($Level) {
  "major" { $major++; $minor = 0; $patch = 0 }
  "minor" { $minor++; $patch = 0 }
  "patch" { $patch++ }
}

$newVersion = "$major.$minor.$patch"
$updated = [regex]::Replace(
  $raw,
  '"version"\s*:\s*"\d+\.\d+\.\d+"',
  "`"version`": `"$newVersion`"",
  1
)

$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($manifestPath, $updated, $utf8NoBom)

Write-Host "Stock Agent version: $oldVersion -> $newVersion ($Level)"
