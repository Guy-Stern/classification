<#
    silent_install.ps1 - headless, unattended installer for MaterialClassification.

    ACTIVATION-PROFILE / SHARED-DATA design (self_contained:false):
    A single self-extracting installer cannot exceed 4 GB (Windows CreateProcess
    limit), but the full offline payload is ~17 GB. So the version-stable heavy
    data - the pip wheels and the model weights (HF cache + SAM3) - lives ONCE
    per box in a shared dir, provisioned out of band (see provision_shared.ps1),
    and this installer (a small ~200 MB artifact: bundled Python + app code)
    builds the venv FROM the shared wheels and points the app at the shared
    weights. Each new version ships only the small code delta.

    Contract (MANAGED_TOOL_INSTALLERS.md), unchanged by the shared-data split:
       * no WinForms/prompts/pauses                    -> (a) Silent
       * install directory is a parameter              -> (b) Redirectable
       * per-user, never elevates                      -> (d) No admin
       * exit 0 only when TRULY finished; ANY pip
         failure is FATAL (not a warning)              -> (e) Honest exit code
       * re-run reproduces / re-establishes; the two
         config files are preserved                    -> (f)/(g)

    Layout under -InstallDir (what MaterialClassification_CLI.exe expects):
        <InstallDir>\ .venv\  cli.py  *.exe  backend\  web_app\  shared\
                     start.bat  app_config.json  shapefile_config.json
    Model weights are NOT copied here - app_config points them at -SharedDir.

    -SharedDir layout (provisioned once per box; default C:\JARVIS\shared\material_classification):
        offline_packages\  offline_packages_torch\  offline_packages_gpu\   (wheels)
        hf_cache\          (HF cache; hub\...)
        sam3\             (sam3.pt + tokenizer files)
        sam3_runtime\bpe_simple_vocab_16e6.txt.gz

    -PayloadDir layout (bundled in this installer):
        silent_install.ps1
        app\ ...            (program tree + exes, NO models\)
        prerequisites\python311\   (full, venv-capable Python 3.11)
        assets\examples\  assets\shapefile_config.example.json
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDir,

    # Root of the extracted installer payload (this script's own folder).
    [string]$PayloadDir = $PSScriptRoot,

    # Shared, per-box heavy data (wheels + weights). Default: derived from
    # InstallDir (<jarvis_root>\shared\material_classification), overridable by
    # the MC_SHARED_DIR env var or this parameter.
    [string]$SharedDir,

    [switch]$Gpu,
    [string]$LogFile
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$PRESERVE_FILES = @('app_config.json', 'shapefile_config.json')

$script:LogPath = $null
function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = ('[{0}] [{1}] {2}' -f (Get-Date -Format 'HH:mm:ss'), $Level, $Message)
    Write-Host $line
    if ($script:LogPath) { try { Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8 } catch { } }
}
function Fail { param([string]$Message); Write-Log $Message 'ERROR'; Write-Log 'INSTALL FAILED.' 'ERROR'; exit 1 }

function Invoke-Native {
    param([Parameter(Mandatory)][string]$Exe, [Parameter(Mandatory)][string[]]$Arguments,
          [Parameter(Mandatory)][string]$What, [switch]$NonFatal)
    Write-Log ("RUN: {0} {1}" -f $Exe, ($Arguments -join ' '))
    # pip/venv write warnings to stderr even on success; under Stop that would abort a SUCCESSFUL
    # install in PS 5.1. Drop to Continue for the call and gate on $LASTEXITCODE (the real signal).
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments 2>&1 | ForEach-Object { Write-Log ("    {0}" -f $_) } }
    finally { $ErrorActionPreference = $prev }
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        if ($NonFatal) { Write-Log ("{0} returned exit {1} (non-fatal, continuing)." -f $What, $code) 'WARN'; return $false }
        Fail ("{0} FAILED (exit {1})." -f $What, $code)
    }
    Write-Log ("{0} OK." -f $What); return $true
}

try {
    if ([string]::IsNullOrWhiteSpace($InstallDir)) { Write-Host '[ERROR] -InstallDir is required.'; exit 2 }
    $InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
    if (-not (Test-Path -LiteralPath $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }

    if (-not $LogFile) { $LogFile = Join-Path $InstallDir 'silent_install.log' }
    $script:LogPath = $LogFile
    Set-Content -LiteralPath $script:LogPath -Value ('=== silent_install {0} ===' -f (Get-Date)) -Encoding UTF8

    #  Resolve the shared data dir 
    if (-not $SharedDir) {
        if ($env:MC_SHARED_DIR) { $SharedDir = $env:MC_SHARED_DIR }
        else {
            # <jarvis_root>\tools\material_classification\<version> -> <jarvis_root>\shared\material_classification
            $jarvisRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $InstallDir))
            $SharedDir = Join-Path $jarvisRoot 'shared\material_classification'
        }
    }
    $SharedDir = [System.IO.Path]::GetFullPath($SharedDir)

    Write-Log ("InstallDir = {0}" -f $InstallDir)
    Write-Log ("PayloadDir = {0}" -f $PayloadDir)
    Write-Log ("SharedDir  = {0}" -f $SharedDir)
    Write-Log ("Gpu={0}" -f [bool]$Gpu)

    $appSrc = Join-Path $PayloadDir 'app'
    $assets = Join-Path $PayloadDir 'assets'
    $pkg = Join-Path $SharedDir 'offline_packages'
    $pkgTorch = Join-Path $SharedDir 'offline_packages_torch'
    $pkgGpu = Join-Path $SharedDir 'offline_packages_gpu'
    $hfCache = Join-Path $SharedDir 'hf_cache'
    $sam3Dir = Join-Path $SharedDir 'sam3'

    if (-not (Test-Path -LiteralPath $appSrc)) { Fail ("Payload missing app\ tree: {0}" -f $appSrc) }
    if (-not (Test-Path -LiteralPath $SharedDir)) {
        Fail ("Shared data dir not found: {0}`n" +
              "Provision it once per box (see provision_shared.ps1 / AIRGAP_A4000_DEPLOY.md), " +
              "or pass -SharedDir / set MC_SHARED_DIR." -f $SharedDir)
    }
    foreach ($need in @($pkg, $pkgTorch)) {
        if (-not (Test-Path -LiteralPath $need)) { Fail ("Shared data incomplete - missing wheels dir: {0}" -f $need) }
    }
    if (-not (Test-Path -LiteralPath $sam3Dir)) { Write-Log ("shared sam3\ weights not found ({0}); SAM3 may not work." -f $sam3Dir) 'WARN' }

    #  Step 1 - venv-capable Python 3.11
    Write-Log '=== [1/9] Locating Python 3.11 (venv-capable) ==='
    $bundledSrc = Join-Path $PayloadDir 'prerequisites\python311'
    $py311 = $null
    if (Test-Path -LiteralPath (Join-Path $bundledSrc 'python.exe')) {
        # The venv's base interpreter must PERSIST after the installer's temp
        # scratch dir is deleted - so copy the bundled Python into the install
        # dir and build the venv from there (a Windows venv references its base
        # for python311.dll + stdlib).
        $persistPy = Join-Path $InstallDir 'python311'
        if (-not (Test-Path -LiteralPath (Join-Path $persistPy 'python.exe'))) {
            Copy-Item -LiteralPath $bundledSrc -Destination $persistPy -Recurse -Force
        }
        $py311 = Join-Path $persistPy 'python.exe'
        Write-Log ("Using bundled Python (copied to {0})" -f $persistPy)
    }
    else {
        foreach ($probe in @(
                @{ Exe = 'py';     Args = @('-3.11', '-c', 'import sys;print(sys.executable)') },
                @{ Exe = 'python'; Args = @('-c', 'import sys;print(sys.version_info[:2]==(3,11) and sys.executable or "")') })) {
            try {
                $out = (& $probe.Exe @($probe.Args) 2>$null | Select-Object -Last 1)
                if ($LASTEXITCODE -eq 0 -and $out -and (Test-Path -LiteralPath $out)) { $py311 = $out.Trim(); break }
            } catch { }
        }
        if ($py311) { Write-Log ("Using system Python: {0}" -f $py311) }
    }
    if (-not $py311) { Fail 'No venv-capable Python 3.11 found (bundled prerequisites\python311 or a system 3.11).' }

    #  Step 2 - .venv 
    Write-Log '=== [2/9] Creating .venv ==='
    $venvDir = Join-Path $InstallDir '.venv'
    $venvPy = Join-Path $venvDir 'Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) { Write-Log '.venv already present - reusing (idempotent re-run).' }
    else {
        if (Test-Path -LiteralPath $venvDir) { Remove-Item -LiteralPath $venvDir -Recurse -Force }
        Invoke-Native -Exe $py311 -Arguments @('-m', 'venv', $venvDir) -What 'python -m venv' | Out-Null
    }
    if (-not (Test-Path -LiteralPath $venvPy)) { Fail ("venv python missing after creation: {0}" -f $venvPy) }

    #  Step 2b - OpenMP exit-139 guard in the venv 
    $siteCustomize = Join-Path $venvDir 'Lib\site-packages\sitecustomize.py'
    $scBody = @'
# Installed by MaterialClassification silent installer.
# Guards the "multiple OpenMP runtimes" crash (exit 139) on this stack.
import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
'@
    Set-Content -LiteralPath $siteCustomize -Value $scBody -Encoding UTF8
    Write-Log 'wrote   venv sitecustomize.py (KMP_DUPLICATE_LIB_OK guard)'

    #  Step 3 - pip/setuptools/wheel (offline from shared, non-fatal) 
    Write-Log '=== [3/9] Upgrading pip/setuptools/wheel (offline, shared) ==='
    Invoke-Native -Exe $venvPy -NonFatal -What 'pip upgrade' -Arguments @(
        '-m', 'pip', 'install', '--no-index', '--find-links', $pkg, '--upgrade', 'pip', 'setuptools', 'wheel') | Out-Null

    #  Step 4 - core requirements (FATAL, from shared wheels) 
    Write-Log '=== [4/9] Core packages ==='
    $reqSrc = Join-Path $appSrc 'requirements.txt'
    if (-not (Test-Path -LiteralPath $reqSrc)) { Fail ("requirements.txt not found in payload: {0}" -f $reqSrc) }
    $tmpReq = Join-Path $InstallDir '_core_requirements.txt'
    Get-Content -LiteralPath $reqSrc |
        Where-Object { $_ -notmatch '^\s*#' } |
        Where-Object { $_ -notmatch 'segment.geospatial' } |
        Where-Object { $_ -notmatch 'triton.windows' } |
        Where-Object { $_ -notmatch 'opencv.python' } |
        Where-Object { $_ -notmatch '^\s*torch' } |
        Where-Object { $_.Trim() -ne '' } |
        Set-Content -LiteralPath $tmpReq -Encoding ASCII
    Invoke-Native -Exe $venvPy -What 'pip install core requirements' -Arguments @(
        '-m', 'pip', 'install', '--no-index', '--find-links', $pkg, '--find-links', $pkgTorch, '-r', $tmpReq) | Out-Null
    Remove-Item -LiteralPath $tmpReq -Force -ErrorAction SilentlyContinue

    #  Step 5 - PyTorch (FATAL, from shared torch wheels) 
    Write-Log '=== [5/9] PyTorch ==='
    Invoke-Native -Exe $venvPy -What 'pip install torch torchvision' -Arguments @(
        '-m', 'pip', 'install', '--no-index', '--find-links', $pkgTorch, '--find-links', $pkg, 'torch', 'torchvision') | Out-Null

    #  Step 6 - SAM3 stack (FATAL, from shared wheels) 
    Write-Log '=== [6/9] SAM3 stack (sam3, timm, triton-windows) ==='
    Invoke-Native -Exe $venvPy -What 'pip install sam3/timm/triton-windows' -Arguments @(
        '-m', 'pip', 'install', '--no-index', '--find-links', $pkg, '--find-links', $pkgTorch, '--find-links', $pkgGpu,
        'sam3', 'timm', 'triton-windows') | Out-Null

    #  Step 7 - GPU KMeans pack (optional) 
    Write-Log '=== [7/9] GPU KMeans pack (CuPy/NVIDIA) ==='
    if ($Gpu -and (Test-Path -LiteralPath $pkgGpu)) {
        Invoke-Native -Exe $venvPy -NonFatal -What 'pip install GPU pack' -Arguments @(
            '-m', 'pip', 'install', '--no-index', '--find-links', $pkgGpu,
            'cupy-cuda12x', 'nvidia-cuda-runtime-cu12', 'nvidia-cublas-cu12', 'nvidia-cuda-nvrtc-cu12', 'nvidia-curand-cu12') | Out-Null
    }
    else { Write-Log 'GPU pack skipped (pass -Gpu to enable; core.py falls back to CPU).' }

    #  Step 8 - copy the program tree (NO models; preserve config) 
    Write-Log '=== [8/9] Copying application files (code only; weights stay shared) ==='
    Get-ChildItem -LiteralPath $appSrc -Force | ForEach-Object {
        $name = $_.Name
        if ($name -eq 'models') { return }  # weights live in -SharedDir, never copied per version
        if ($PRESERVE_FILES -contains $name -and (Test-Path -LiteralPath (Join-Path $InstallDir $name))) {
            Write-Log ("PRESERVE: {0} exists - leaving untouched." -f $name); return
        }
        $dst = Join-Path $InstallDir $name
        if ($_.PSIsContainer) { Copy-Item -LiteralPath $_.FullName -Destination $dst -Recurse -Force }
        else { Copy-Item -LiteralPath $_.FullName -Destination $dst -Force }
        Write-Log ("copied  {0}" -f $name)
    }

    # BPE tokenizer asset for SAM3 (from shared sam3_runtime, else bundled assets).
    $bpeSrc = Join-Path $SharedDir 'sam3_runtime\bpe_simple_vocab_16e6.txt.gz'
    if (-not (Test-Path -LiteralPath $bpeSrc)) { $bpeSrc = Join-Path $assets 'sam3_runtime\bpe_simple_vocab_16e6.txt.gz' }
    $assetsDir = Join-Path $venvDir 'Lib\site-packages\assets'
    if (Test-Path -LiteralPath $bpeSrc) {
        if (-not (Test-Path -LiteralPath $assetsDir)) { New-Item -ItemType Directory -Path $assetsDir -Force | Out-Null }
        Copy-Item -LiteralPath $bpeSrc -Destination $assetsDir -Force
        Write-Log 'copied  bpe_simple_vocab_16e6.txt.gz -> venv site-packages\assets\'
    }
    else { Write-Log 'BPE tokenizer asset not found (SAM3 will be slower).' 'WARN' }

    $examplesSrc = Join-Path $assets 'examples'
    if (Test-Path -LiteralPath $examplesSrc) {
        Copy-Item -LiteralPath $examplesSrc -Destination (Join-Path $InstallDir 'examples') -Recurse -Force
        Write-Log 'copied  examples\'
    }

    #  Step 9 - configs (point at shared weights) + start.bat 
    Write-Log '=== [9/9] Configs and start.bat ==='
    $appCfg = Join-Path $InstallDir 'app_config.json'
    if (-not (Test-Path -LiteralPath $appCfg)) {
        $cfg = [ordered]@{ sam3_local_dir = $sam3Dir; hf_cache_dir = $hfCache; offline_mode = $true }
        ($cfg | ConvertTo-Json) | Set-Content -LiteralPath $appCfg -Encoding UTF8
        Write-Log ("created  app_config.json (sam3_local_dir + hf_cache_dir -> shared)")
    }
    else { Write-Log 'PRESERVE: app_config.json exists - left untouched.' }

    $shpCfg = Join-Path $InstallDir 'shapefile_config.json'
    $shpExampleSrc = Join-Path $assets 'shapefile_config.example.json'
    if (-not (Test-Path -LiteralPath $shpCfg)) {
        if (Test-Path -LiteralPath $shpExampleSrc) { Copy-Item -LiteralPath $shpExampleSrc -Destination $shpCfg -Force; Write-Log 'created  shapefile_config.json (from example)' }
        else { '{ "buildings": [], "roads": [], "water": [] }' | Set-Content -LiteralPath $shpCfg -Encoding UTF8; Write-Log 'created  shapefile_config.json (empty)' }
    }
    else { Write-Log 'PRESERVE: shapefile_config.json exists - left untouched.' }
    if (Test-Path -LiteralPath $shpExampleSrc) { Copy-Item -LiteralPath $shpExampleSrc -Destination (Join-Path $InstallDir 'shapefile_config.example.json') -Force }

    # start.bat - .venv python + HF_HOME at the shared cache.
    $startBat = @"
@echo off
cd /d "%~dp0"
echo Starting Classification Web App...
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
set HF_HOME=$hfCache
set PYTHONPATH=%~dp0
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
"@
    Set-Content -LiteralPath (Join-Path $InstallDir 'start.bat') -Value $startBat -Encoding ASCII
    Write-Log 'wrote   start.bat'

    #  Smoke check 
    Write-Log '=== smoke check ==='
    $cliExe = Join-Path $InstallDir 'MaterialClassification_CLI.exe'
    if (-not (Test-Path -LiteralPath $cliExe)) { Fail ("entry executable missing: {0}" -f $cliExe) }
    Invoke-Native -Exe $cliExe -Arguments @('--examples') -What 'CLI entry (--examples)' | Out-Null
    Push-Location -LiteralPath $InstallDir
    try {
        $env:PYTHONPATH = $InstallDir
        Invoke-Native -Exe $venvPy -What 'import torch + backend.app.core' -Arguments @(
            '-c', 'import torch, numpy, rasterio, sklearn; import backend.app.core') | Out-Null
    }
    finally { Pop-Location }

    Write-Log ("INSTALL SUCCESSFUL -> {0}" -f $InstallDir)
    exit 0
}
catch { Fail ("Unhandled error: {0}`n{1}" -f $_.Exception.Message, $_.ScriptStackTrace) }
