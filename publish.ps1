<#
    publish.ps1 - one command to publish a version.

    This is the wired-in "every version" step: it produces BOTH the existing
    interactive installer bundle AND the JARVIS-managed silent installer +
    recipe.json, stamps a real version everywhere, and assembles a single
    versioned folder under Publishes\.

    It does NOT modify the interactive installer - it wraps the existing
    build_exe.bat + prepare_offline.bat and adds the silent artifacts.

    Version source: git tag on HEAD, else `git describe --tags`, else VERSION.

    Typical use (interactive prepare_offline prompts for torch/GPU as today):
        .\publish.ps1

    Re-publish without rebuilding the heavy offline tree:
        .\publish.ps1 -SkipExeBuild -SkipPrepareOffline
#>
[CmdletBinding()]
param(
    [switch]$SkipExeBuild,        # skip build_exe.bat (reuse existing *.exe)
    [switch]$SkipPrepareOffline,  # skip prepare_offline.bat (reuse offline_installer\)
    [switch]$SkipSilent,          # skip building the silent exe (NOT recommended)
    [switch]$SkipSelfTest,        # forward to build_silent_installer (NOT recommended)
    [string]$Version,             # override the resolved version
    [string]$OutRoot = 'Publishes',
    [switch]$Force                # overwrite an existing Publishes\<version> folder
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $root) { $root = $PSScriptRoot }
Set-Location $root
function Say($m)  { Write-Host "`n=== [publish] $m ===" -ForegroundColor Cyan }
function Die($m)  { Write-Host "[publish] ERROR: $m" -ForegroundColor Red; exit 1 }

# -- 1. Resolve version (git tag -> VERSION) ----------------------------------
function Resolve-Version {
    # Suppress git's "No names found" stderr locally (would throw under
    # ErrorActionPreference=Stop in PS 5.1); gate strictly on $LASTEXITCODE.
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
Say "version = $Version"

$outDir = Join-Path (Join-Path $root $OutRoot) $Version
if ((Test-Path -LiteralPath $outDir) -and -not $Force) {
    Die "Publishes\$Version already exists. Use -Force to overwrite (did you forget to bump VERSION / tag?)."
}

# -- 2. Build the exes --------------------------------------------------------
if (-not $SkipExeBuild) {
    Say 'build_exe.bat'
    & cmd /c (Join-Path $root 'build_exe.bat')
    if ($LASTEXITCODE -ne 0) { Die "build_exe.bat failed ($LASTEXITCODE)." }
}
else { Say 'build_exe.bat SKIPPED' }

# -- 3. Build the offline tree (interactive: torch/GPU prompts) ---------------
if (-not $SkipPrepareOffline) {
    Say 'prepare_offline.bat'
    & cmd /c (Join-Path $root 'prepare_offline.bat')
    if ($LASTEXITCODE -ne 0) { Die "prepare_offline.bat failed ($LASTEXITCODE)." }
}
else { Say 'prepare_offline.bat SKIPPED' }

# -- 4. Stamp the real version (replace prepare_offline's hardcoded 1.0.0) ----
$verTxt = Join-Path $root 'offline_installer\app\version.txt'
if (Test-Path -LiteralPath (Split-Path $verTxt)) {
    Set-Content -LiteralPath $verTxt -Value $Version -Encoding ASCII
    Say "stamped version.txt = $Version"
}

# -- 5. Build the silent installer exe ----------------------------------------
$silentExe = Join-Path $root 'MaterialClassification_Silent_Setup.exe'
if (-not $SkipSilent) {
    Say 'build_silent_installer.ps1'
    $bsArgs = @('-OutFile', $silentExe)
    if ($SkipSelfTest) { $bsArgs += '-SkipSelfTest' }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'installer_silent\build_silent_installer.ps1') @bsArgs
    if ($LASTEXITCODE -ne 0) { Die "build_silent_installer.ps1 failed ($LASTEXITCODE)." }
    if (-not (Test-Path -LiteralPath $silentExe)) { Die 'silent exe not produced.' }
}
else { Say 'silent installer SKIPPED' }

# -- 6. Emit the recipe -------------------------------------------------------
$recipe = Join-Path $root 'recipe.json'
Say 'make_recipe.ps1'
$mkArgs = @('-Version', $Version, '-ArtifactPath', (Split-Path -Leaf $silentExe), '-OutFile', $recipe)
$sample = 'C:\ClassificationApp'
if (Test-Path -LiteralPath $sample) { $mkArgs += @('-InstallSample', $sample) }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'installer_silent\make_recipe.ps1') @mkArgs
if ($LASTEXITCODE -ne 0) { Die "make_recipe.ps1 failed ($LASTEXITCODE)." }

# -- 7. Assemble Publishes\<version>\ -----------------------------------------
Say "assembling $outDir"
if (Test-Path -LiteralPath $outDir) { Remove-Item -LiteralPath $outDir -Recurse -Force }
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

# Interactive bundle (mirror what prepare_offline drops at the project root).
$bundle = @(
    'offline_installer', 'ClassificationInstaller.exe', 'Post-Install.bat',
    'README.txt', 'shapefile_config.example.json', 'sam3_runtime', 'examples'
)
foreach ($item in $bundle) {
    $src = Join-Path $root $item
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $outDir $item) -Recurse -Force
    }
    else { Write-Host "[publish] note: $item not present, skipping" }
}

# Managed silent installer + recipe + version marker.
if (Test-Path -LiteralPath $silentExe) { Copy-Item -LiteralPath $silentExe -Destination $outDir -Force }
Copy-Item -LiteralPath $recipe -Destination $outDir -Force
Set-Content -LiteralPath (Join-Path $outDir 'VERSION') -Value $Version -Encoding ASCII

# The silent installer is NOT standalone: it exits 1 unless the shared data was
# provisioned first. Ship the provisioning script + its runbook alongside it, or
# the operator on the target box has the installer and no way to satisfy it.
# SHARED_SHAPEFILE_CONFIG ships as the rendered PDF (the .md stays the source in
# docs\); rebuild it after editing with:
#   .venv\Scripts\python.exe tools\build_cli_guide_pdf.py docs\SHARED_SHAPEFILE_CONFIG.md docs\SHARED_SHAPEFILE_CONFIG.pdf
foreach ($extra in @('installer_silent\provision_shared.ps1', 'docs\AIRGAP_A4000_DEPLOY.md',
                     'docs\SHARED_SHAPEFILE_CONFIG.pdf')) {
    $src = Join-Path $root $extra
    if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $outDir -Force }
    else { Write-Host "[publish] WARNING: $extra missing - the bundle will not be self-sufficient." }
}

Say 'DONE'
Write-Host "  Published version : $Version"
Write-Host "  Folder            : $outDir"
Write-Host "  Silent installer  : MaterialClassification_Silent_Setup.exe"
Write-Host "  Recipe            : recipe.json"
Write-Host ""
Write-Host "Next: upload the silent exe to the artifact share, then release the" -ForegroundColor Yellow
Write-Host "recipe via the Workshop (PUBLISHING.md Step 4b) - set artifact_path to" -ForegroundColor Yellow
Write-Host "the share path. See installer_silent\README.md." -ForegroundColor Yellow
