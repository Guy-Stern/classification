<#
    make_recipe.ps1

    Emits the release-time recipe.json for the JARVIS-managed silent installer
    (MANAGED_TOOL_INSTALLERS.md 4 / 8). Run once per published version.

    Version source (as configured for this project):
        1. an exact git tag on HEAD (e.g. v1.2.0)   -> "1.2.0"
        2. otherwise `git describe --tags`           -> "1.2.0-5-gabc123"
        3. otherwise the repo-root VERSION file

    Footprint: measured from -InstallSample if given, else the last measured
    value (C:\ClassificationApp ~= 18.8 GB) rounded up for a safety margin -
    under-counting risks a mid-install disk failure (per the doc).
#>
[CmdletBinding()]
param(
    # Where the built silent exe lives / will live on the share. The basename is
    # what goes in the recipe unless -FullArtifactPath is given.
    [string]$ArtifactPath = 'MaterialClassification_Silent_Setup.exe',
    [switch]$FullArtifactPath,

    # Optional: a real installed tree to measure for install_footprint_bytes.
    [string]$InstallSample,

    # Where to write the recipe. Defaults to <repo root>\recipe.json.
    [string]$OutFile,

    # Override the resolved version (rarely needed).
    [string]$Version
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Resolve paths in the body ($PSScriptRoot is unreliable in param defaults).
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDir) { $scriptDir = $PSScriptRoot }
$root    = (Resolve-Path (Join-Path $scriptDir '..')).Path
if (-not $OutFile) { $OutFile = Join-Path $root 'recipe.json' }
$OutFile = [System.IO.Path]::GetFullPath($OutFile)

function Die($m) { Write-Host "[make-recipe] ERROR: $m" -ForegroundColor Red; exit 1 }

# -- Resolve version: git tag -> VERSION file ---------------------------------
function Resolve-Version {
    # git writes "No names found" to stderr when untagged; under
    # ErrorActionPreference=Stop that would throw in PS 5.1, so suppress locally
    # and gate strictly on $LASTEXITCODE.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        $exact = (& git -C $root describe --tags --exact-match 2>&1 | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and $exact) { return (([string]$exact) -replace '^v', '').Trim() }
        $desc = (& git -C $root describe --tags 2>&1 | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and $desc) { return (([string]$desc) -replace '^v', '').Trim() }
    }
    finally { $ErrorActionPreference = $prev }
    $vf = Join-Path $root 'VERSION'
    if (Test-Path -LiteralPath $vf) {
        $v = (Get-Content -LiteralPath $vf -Raw).Trim()
        if ($v) { return $v }
    }
    Die 'Could not resolve a version: no git tag and no non-empty VERSION file.'
}

if (-not $Version) { $Version = Resolve-Version }
Write-Host "[make-recipe] version = $Version"

# -- Footprint (the VERSIONED tree only: .venv + code; weights are shared) -----
$footprint = 7000000000  # ~7 GB safe default; measure a real install and re-release
if ($InstallSample -and (Test-Path -LiteralPath $InstallSample)) {
    Write-Host "[make-recipe] measuring footprint of $InstallSample ..."
    $sum = (Get-ChildItem -LiteralPath $InstallSample -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    if ($sum -gt 0) {
        # +8% headroom so the agent's disk preflight never under-counts.
        $footprint = [int64]([math]::Ceiling($sum * 1.08))
    }
}
Write-Host "[make-recipe] install_footprint_bytes = $footprint"

# -- artifact_path (basename by default) --------------------------------------
$artifact = if ($FullArtifactPath) { [System.IO.Path]::GetFullPath($ArtifactPath) }
            else { Split-Path -Leaf $ArtifactPath }

# -- Recipe - EXACTLY the documented fields (4) ------------------------------
$recipe = [ordered]@{
    version                 = $Version
    artifact_path           = $artifact
    packaging               = 'installer'          # explicit, never derived
    entry                   = 'MaterialClassification_CLI.exe'
    install_args            = '-InstallDir {INSTALL_DIR}'
    install_footprint_bytes = $footprint
    self_contained          = $false
    preserve                = @('app_config.json', 'shapefile_config.json')
}

$json = $recipe | ConvertTo-Json -Depth 4
Set-Content -LiteralPath $OutFile -Value $json -Encoding UTF8
Write-Host "[make-recipe] wrote $OutFile"

# -- Convenience: print the artifact sha256 (JARVIS hashes it server-side; this
#    just lets the operator verify the upload). Not part of the recipe schema. -
$exe = if ($FullArtifactPath) { $ArtifactPath } else { Join-Path $root $ArtifactPath }
if (Test-Path -LiteralPath $exe) {
    $h = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash
    Write-Host "[make-recipe] artifact sha256 = $h"
}

Write-Output $OutFile
