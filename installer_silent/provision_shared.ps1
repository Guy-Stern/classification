<#
    provision_shared.ps1 - stage the per-box SHARED data for material_classification.

    Run ONCE per worker box (out of band, not via JARVIS). It copies the heavy,
    version-stable data - the pip wheels and the model weights (HF cache + SAM3) -
    into the shared dir the silent installer reads from. After this, JARVIS's
    small managed installer builds each version's venv from these wheels and
    points the app at these weights.

    Source: a `Publishes\InstallerV<N>` folder (has offline_installer\ + sam3_runtime\),
    or an offline_installer\ folder directly.

    Example (on the A4000, from the carried USB):
        powershell -File provision_shared.ps1 -Source E:\InstallerV19 -Gpu
        # -> C:\JARVIS\shared\material_classification\{offline_packages*, hf_cache, sam3, sam3_runtime}
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [string]$Dest = 'C:\JARVIS\shared\material_classification',
    [switch]$Gpu,
    [switch]$UseJunctions   # dev/test only: link instead of copy (no admin needed for /J)
)

$ErrorActionPreference = 'Stop'
function Info($m) { Write-Host "[provision-shared] $m" }
function Die($m)  { Write-Host "[provision-shared] ERROR: $m" -ForegroundColor Red; exit 1 }

$Source = [System.IO.Path]::GetFullPath($Source)
$Dest = [System.IO.Path]::GetFullPath($Dest)

# Locate the offline_installer tree + the sam3_runtime assets under Source.
if (Test-Path -LiteralPath (Join-Path $Source 'offline_installer\app')) {
    $oi = Join-Path $Source 'offline_installer'
    $sam3rt = Join-Path $Source 'sam3_runtime'
}
elseif (Test-Path -LiteralPath (Join-Path $Source 'app')) {
    $oi = $Source
    $sam3rt = Join-Path (Split-Path -Parent $Source) 'sam3_runtime'
}
else { Die "Source doesn't look like a publish or offline_installer folder: $Source" }

# (source relative to $oi/root, dest name)
$map = @(
    @{ Src = (Join-Path $oi 'offline_packages');       Dst = 'offline_packages';       Req = $true },
    @{ Src = (Join-Path $oi 'offline_packages_torch'); Dst = 'offline_packages_torch'; Req = $true },
    @{ Src = (Join-Path $oi 'offline_packages_gpu');   Dst = 'offline_packages_gpu';   Req = $Gpu.IsPresent },
    @{ Src = (Join-Path $oi 'app\models\hf_cache');    Dst = 'hf_cache';               Req = $true },
    @{ Src = (Join-Path $oi 'app\models\sam3');        Dst = 'sam3';                   Req = $true },
    @{ Src = $sam3rt;                                  Dst = 'sam3_runtime';           Req = $false }
)

New-Item -ItemType Directory -Path $Dest -Force | Out-Null
foreach ($m in $map) {
    $dstPath = Join-Path $Dest $m.Dst
    if (-not (Test-Path -LiteralPath $m.Src)) {
        if ($m.Req) { Die "required source missing: $($m.Src)" }
        Info "optional source missing, skipping: $($m.Src)"
        continue
    }
    if ($UseJunctions) {
        if (Test-Path -LiteralPath $dstPath) { & cmd /c rmdir "$dstPath" 2>$null }
        & cmd /c mklink /J "$dstPath" "$($m.Src)" | Out-Null
        Info "linked  $($m.Dst)"
    }
    else {
        Info "copying $($m.Dst) ..."
        & robocopy $m.Src $dstPath /E /NFL /NDL /NJH /NJS /NP /R:2 /W:2 | Out-Null
        if ($LASTEXITCODE -ge 8) { Die "robocopy failed for $($m.Dst) (exit $LASTEXITCODE)" }
    }
}

# Verify.
foreach ($need in @('offline_packages', 'offline_packages_torch', 'hf_cache', 'sam3')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Dest $need))) { Die "shared dir incomplete after provisioning: missing $need" }
}
$sizeGB = [math]::Round(((Get-ChildItem -LiteralPath $Dest -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum) / 1GB, 2)
Info "DONE: shared data at $Dest  (${sizeGB} GB)"
Info "The installer's app_config.json will point sam3_local_dir + hf_cache_dir here."
