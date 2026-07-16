<#
    silent_install.ps1 - headless, unattended installer for MaterialClassification.

    This is the JARVIS-managed silent installer (see MANAGED_TOOL_INSTALLERS.md).
    It does, in one non-interactive pass, EVERYTHING the interactive Setup.ps1 +
    Post-Install.bat do together, but:
       * no WinForms, no prompts, no pauses            -> clause (a) Silent
       * install directory is a parameter              -> clause (b) Redirectable
       * per-user, never elevates                      -> clause (d) No admin
       * exits 0 only when TRULY finished, non-zero
         on ANY failure (pip failures are FATAL here,
         not warnings)                                 -> clause (e) Honest exit code
       * a re-run reproduces / re-establishes the
         install; app_config.json + shapefile_config.json
         are preserved                                 -> clause (f)/(g) Deterministic/idempotent

    The interactive installer (Setup.ps1 / ClassificationInstaller.exe) is left
    untouched - this is a SEPARATE entry point for the managed fleet.

    Layout produced under -InstallDir (what MaterialClassification_CLI.exe expects):
        <InstallDir>\
            .venv\Scripts\python.exe        real venv (NOT embeddable python)
            cli.py  cli_launcher.py  launcher.py  sde_conn_test.py
            MaterialClassification_CLI.exe  ClassificationWebApp.exe
            backend\  web_app\  shared\  models\
            start.bat
            app_config.json  shapefile_config.json   (preserved, never clobbered)

    Expected -PayloadDir layout (produced by build_silent_installer.ps1):
        <PayloadDir>\
            silent_install.ps1  silent_install.cmd
            app\ ...                             (the program tree + models\)
            offline_packages\  offline_packages_torch\  offline_packages_gpu\
            prerequisites\python311\             (full, venv-capable Python 3.11)
            assets\sam3_runtime\bpe_simple_vocab_16e6.txt.gz
            assets\examples\
            assets\shapefile_config.example.json
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    # Where the tool tree is installed. JARVIS passes C:\JARVIS\tools\<tool>\<version>.
    [Parameter(Mandatory = $true)]
    [string]$InstallDir,

    # Root of the extracted payload. Defaults to this script's own folder
    # (the SFX extracts everything side-by-side and runs us from there).
    [string]$PayloadDir = $PSScriptRoot,

    # Install the optional CuPy/NVIDIA GPU KMeans pack (non-fatal if it fails -
    # core.py falls back to CPU sklearn, per _probe_acceleration).
    [switch]$Gpu,

    # Advanced: skip copying the ~5 GB HF/SAM3 weights into the install tree
    # (for a shared-weights deployment where hf_cache_dir points elsewhere).
    [switch]$SkipModelCopy,

    # Optional explicit log path. Defaults to <InstallDir>\silent_install.log.
    [string]$LogFile
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# -- Config the installer must NEVER overwrite on a re-run (the recipe's
#    "preserve" list - operator-tuned, per-box). -----------------------------
$PRESERVE_FILES = @('app_config.json', 'shapefile_config.json')

# -- Logging -----------------------------------------------------------------
$script:LogPath = $null

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = ('[{0}] [{1}] {2}' -f (Get-Date -Format 'HH:mm:ss'), $Level, $Message)
    Write-Host $line
    if ($script:LogPath) {
        try { Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8 } catch { }
    }
}

function Fail {
    param([string]$Message)
    Write-Log $Message 'ERROR'
    Write-Log 'INSTALL FAILED.' 'ERROR'
    exit 1
}

# Run a native command and hard-fail if it returns non-zero.
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$What,
        [switch]$NonFatal
    )
    Write-Log ("RUN: {0} {1}" -f $Exe, ($Arguments -join ' '))
    # pip/venv write warnings to stderr even on success; under
    # ErrorActionPreference=Stop, PS 5.1 would turn those stderr lines into a
    # terminating error and abort a SUCCESSFUL install. Drop to Continue for the
    # call and rely on $LASTEXITCODE (the real signal) instead.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object { Write-Log ("    {0}" -f $_) }
    }
    finally { $ErrorActionPreference = $prev }
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        if ($NonFatal) {
            Write-Log ("{0} returned exit {1} (non-fatal, continuing)." -f $What, $code) 'WARN'
            return $false
        }
        Fail ("{0} FAILED (exit {1})." -f $What, $code)
    }
    Write-Log ("{0} OK." -f $What)
    return $true
}

# -- Pre-flight --------------------------------------------------------------
try {
    if ([string]::IsNullOrWhiteSpace($InstallDir)) {
        Write-Host '[ERROR] -InstallDir is required.'
        exit 2
    }
    # Normalise to a full path.
    $InstallDir = [System.IO.Path]::GetFullPath($InstallDir)

    if (-not (Test-Path -LiteralPath $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }

    if (-not $LogFile) { $LogFile = Join-Path $InstallDir 'silent_install.log' }
    $script:LogPath = $LogFile
    # Fresh log per run.
    Set-Content -LiteralPath $script:LogPath -Value ('=== silent_install {0} ===' -f (Get-Date)) -Encoding UTF8

    Write-Log ("InstallDir = {0}" -f $InstallDir)
    Write-Log ("PayloadDir = {0}" -f $PayloadDir)
    Write-Log ("Gpu={0}  SkipModelCopy={1}" -f [bool]$Gpu, [bool]$SkipModelCopy)

    if (-not (Test-Path -LiteralPath $PayloadDir)) { Fail ("PayloadDir not found: {0}" -f $PayloadDir) }

    $appSrc  = Join-Path $PayloadDir 'app'
    $pkg     = Join-Path $PayloadDir 'offline_packages'
    $pkgTorch = Join-Path $PayloadDir 'offline_packages_torch'
    $pkgGpu  = Join-Path $PayloadDir 'offline_packages_gpu'
    $assets  = Join-Path $PayloadDir 'assets'

    if (-not (Test-Path -LiteralPath $appSrc)) { Fail ("Payload missing app\ tree: {0}" -f $appSrc) }
    if (-not (Test-Path -LiteralPath $pkg))    { Fail ("Payload missing offline_packages\: {0}" -f $pkg) }

    # -- Step 1 - locate a venv-capable Python 3.11 --------------------------
    Write-Log '=== [1/9] Locating Python 3.11 (venv-capable) ==='
    $bundledPy = Join-Path $PayloadDir 'prerequisites\python311\python.exe'
    $py311 = $null
    if (Test-Path -LiteralPath $bundledPy) {
        $py311 = $bundledPy
        Write-Log ("Using bundled Python: {0}" -f $py311)
    }
    else {
        # Fall back to a system Python 3.11 (py launcher, then PATH python).
        foreach ($probe in @(
                @{ Exe = 'py';     Args = @('-3.11', '-c', 'import sys;print(sys.executable)') },
                @{ Exe = 'python'; Args = @('-c', 'import sys;print(sys.version_info[:2]==(3,11) and sys.executable or "")') }
            )) {
            try {
                $out = (& $probe.Exe @($probe.Args) 2>$null | Select-Object -Last 1)
                if ($LASTEXITCODE -eq 0 -and $out -and (Test-Path -LiteralPath $out)) {
                    $py311 = $out.Trim(); break
                }
            } catch { }
        }
        if ($py311) { Write-Log ("Using system Python: {0}" -f $py311) }
    }
    if (-not $py311) {
        Fail ('No venv-capable Python 3.11 found. Expected bundled ' +
              'prerequisites\python311\python.exe (staged by prepare_offline.bat) ' +
              'or a system Python 3.11 on PATH.')
    }

    # -- Step 2 - create the .venv -------------------------------------------
    Write-Log '=== [2/9] Creating .venv ==='
    $venvDir = Join-Path $InstallDir '.venv'
    $venvPy  = Join-Path $venvDir 'Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) {
        Write-Log '.venv already present - reusing (idempotent re-run).'
    }
    else {
        if (Test-Path -LiteralPath $venvDir) { Remove-Item -LiteralPath $venvDir -Recurse -Force }
        Invoke-Native -Exe $py311 -Arguments @('-m', 'venv', $venvDir) -What 'python -m venv' | Out-Null
    }
    if (-not (Test-Path -LiteralPath $venvPy)) { Fail ("venv python missing after creation: {0}" -f $venvPy) }

    # -- Step 2b - venv sitecustomize: guard the OpenMP exit-139 segfault ----
    #    torch / numpy-MKL / faiss each load their own libiomp; on this stack
    #    that segfaults at KMeans unless KMP_DUPLICATE_LIB_OK=TRUE (see
    #    run_demo.bat). JARVIS runs the CLI directly with no env prep, so we
    #    bake the guard into the venv itself - auto-loaded on every python
    #    startup, overridable by the operator via a real env var.
    $siteCustomize = Join-Path $venvDir 'Lib\site-packages\sitecustomize.py'
    $scBody = @'
# Installed by MaterialClassification silent installer.
# Guards the "multiple OpenMP runtimes" crash (exit 139) on this stack.
import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
'@
    Set-Content -LiteralPath $siteCustomize -Value $scBody -Encoding UTF8
    Write-Log 'wrote   venv sitecustomize.py (KMP_DUPLICATE_LIB_OK guard)'

    # -- Step 3 - upgrade pip/setuptools/wheel (offline, non-fatal) ----------
    Write-Log '=== [3/9] Upgrading pip/setuptools/wheel (offline) ==='
    Invoke-Native -Exe $venvPy -NonFatal -What 'pip upgrade' -Arguments @(
        '-m', 'pip', 'install', '--no-index', '--find-links', $pkg,
        '--upgrade', 'pip', 'setuptools', 'wheel') | Out-Null

    # -- Step 4 - core requirements (FATAL) ----------------------------------
    #    Filter out comment/blank lines and the packages we install separately
    #    (torch in step 5; sam3/triton/opencv in step 6) - matches Setup.ps1.
    Write-Log '=== [4/9] Core packages ==='
    $reqSrc = Join-Path $appSrc 'requirements.txt'
    if (Test-Path -LiteralPath $reqSrc) {
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
            '-m', 'pip', 'install', '--no-index',
            '--find-links', $pkg, '--find-links', $pkgTorch,
            '-r', $tmpReq) | Out-Null
        Remove-Item -LiteralPath $tmpReq -Force -ErrorAction SilentlyContinue
    }
    else {
        Fail ("requirements.txt not found in payload: {0}" -f $reqSrc)
    }

    # -- Step 5 - PyTorch (FATAL) --------------------------------------------
    #    cu121 wheels from offline_packages_torch (work on the A4000's 12.4
    #    driver via forward-compat); fall back to offline_packages for CPU.
    Write-Log '=== [5/9] PyTorch ==='
    $torchLinks = @('--find-links', $pkg)
    if (Test-Path -LiteralPath $pkgTorch) { $torchLinks = @('--find-links', $pkgTorch, '--find-links', $pkg) }
    Invoke-Native -Exe $venvPy -What 'pip install torch torchvision' -Arguments (@(
        '-m', 'pip', 'install', '--no-index') + $torchLinks + @('torch', 'torchvision')) | Out-Null

    # -- Step 6 - SAM3 stack (FATAL) -----------------------------------------
    #    sam3 + timm + triton-windows. Deliberately NOT segment-geospatial -
    #    it pulls 22+ transitive deps and slows SAM3 inference ~20x (see
    #    Post-Install.bat). A managed tool must be usable the instant this
    #    installer exits, so the SAM3 stack is FATAL here (not a warning).
    Write-Log '=== [6/9] SAM3 stack (sam3, timm, triton-windows) ==='
    Invoke-Native -Exe $venvPy -What 'pip install sam3/timm/triton-windows' -Arguments @(
        '-m', 'pip', 'install', '--no-index',
        '--find-links', $pkg, '--find-links', $pkgTorch, '--find-links', $pkgGpu,
        'sam3', 'timm', 'triton-windows') | Out-Null

    # -- Step 7 - GPU KMeans pack (optional, non-fatal) ----------------------
    Write-Log '=== [7/9] GPU KMeans pack (CuPy/NVIDIA) ==='
    if ($Gpu -and (Test-Path -LiteralPath $pkgGpu)) {
        Invoke-Native -Exe $venvPy -NonFatal -What 'pip install GPU pack' -Arguments @(
            '-m', 'pip', 'install', '--no-index', '--find-links', $pkgGpu,
            'cupy-cuda12x', 'nvidia-cuda-runtime-cu12', 'nvidia-cublas-cu12',
            'nvidia-cuda-nvrtc-cu12', 'nvidia-curand-cu12') | Out-Null
    }
    else {
        Write-Log 'GPU pack skipped (pass -Gpu to enable; core.py falls back to CPU).'
    }

    # -- Step 8 - copy the program tree (preserving per-box config) ----------
    Write-Log '=== [8/9] Copying application files ==='
    Get-ChildItem -LiteralPath $appSrc -Force | ForEach-Object {
        $name = $_.Name
        if ($PRESERVE_FILES -contains $name -and (Test-Path -LiteralPath (Join-Path $InstallDir $name))) {
            Write-Log ("PRESERVE: {0} already exists at install root - leaving untouched." -f $name)
            return
        }
        if ($SkipModelCopy -and $name -eq 'models') {
            Write-Log 'SkipModelCopy: not copying models\ (shared-weights mode).'
            return
        }
        $dst = Join-Path $InstallDir $name
        if ($_.PSIsContainer) {
            Copy-Item -LiteralPath $_.FullName -Destination $dst -Recurse -Force
        }
        else {
            Copy-Item -LiteralPath $_.FullName -Destination $dst -Force
        }
        Write-Log ("copied  {0}" -f $name)
    }

    # BPE tokenizer asset for SAM3 - must land at <venv>\Lib\site-packages\assets\.
    $bpeSrc = Join-Path $assets 'sam3_runtime\bpe_simple_vocab_16e6.txt.gz'
    $assetsDir = Join-Path $venvDir 'Lib\site-packages\assets'
    if (Test-Path -LiteralPath $bpeSrc) {
        if (-not (Test-Path -LiteralPath $assetsDir)) { New-Item -ItemType Directory -Path $assetsDir -Force | Out-Null }
        Copy-Item -LiteralPath $bpeSrc -Destination $assetsDir -Force
        Write-Log 'copied  bpe_simple_vocab_16e6.txt.gz -> venv site-packages\assets\'
    }
    else {
        # Not fatal, but loud: without it SAM3 falls back to OWLv2+SAM2 (~10x slower).
        Write-Log ("BPE tokenizer asset missing from payload ({0}); SAM3 will be slow." -f $bpeSrc) 'WARN'
    }

    # Usage examples (optional).
    $examplesSrc = Join-Path $assets 'examples'
    if (Test-Path -LiteralPath $examplesSrc) {
        Copy-Item -LiteralPath $examplesSrc -Destination (Join-Path $InstallDir 'examples') -Recurse -Force
        Write-Log 'copied  examples\'
    }

    # -- Step 9 - configs + start.bat ----------------------------------------
    Write-Log '=== [9/9] Configs and start.bat ==='

    # app_config.json - write a default ONLY if absent (preserve otherwise).
    $appCfg = Join-Path $InstallDir 'app_config.json'
    if (-not (Test-Path -LiteralPath $appCfg)) {
        $cfg = [ordered]@{ sam3_local_dir = $null; hf_cache_dir = $null; offline_mode = $true }
        ($cfg | ConvertTo-Json) | Set-Content -LiteralPath $appCfg -Encoding UTF8
        Write-Log 'created  app_config.json (defaults)'
    }
    else { Write-Log 'PRESERVE: app_config.json exists - left untouched.' }

    # shapefile_config.json - template only if absent; ship the .example.json.
    $shpCfg = Join-Path $InstallDir 'shapefile_config.json'
    $shpExampleSrc = Join-Path $assets 'shapefile_config.example.json'
    if (-not (Test-Path -LiteralPath $shpCfg)) {
        if (Test-Path -LiteralPath $shpExampleSrc) {
            Copy-Item -LiteralPath $shpExampleSrc -Destination $shpCfg -Force
            Write-Log 'created  shapefile_config.json (from example template)'
        }
        else {
            '{ "buildings": [], "roads": [], "water": [] }' | Set-Content -LiteralPath $shpCfg -Encoding UTF8
            Write-Log 'created  shapefile_config.json (empty template)'
        }
    }
    else { Write-Log 'PRESERVE: shapefile_config.json exists - left untouched.' }
    if (Test-Path -LiteralPath $shpExampleSrc) {
        Copy-Item -LiteralPath $shpExampleSrc -Destination (Join-Path $InstallDir 'shapefile_config.example.json') -Force
    }

    # start.bat - uses the .venv python (fixes the embedded-python\ bug in Setup.ps1).
    $startBat = @'
@echo off
cd /d "%~dp0"
echo Starting Classification Web App...
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
set HF_HOME=%~dp0models\hf_cache
set PYTHONPATH=%~dp0
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
'@
    Set-Content -LiteralPath (Join-Path $InstallDir 'start.bat') -Value $startBat -Encoding ASCII
    Write-Log 'wrote   start.bat'

    # -- Smoke check - prove the install is usable the instant we exit -------
    Write-Log '=== smoke check ==='
    $cliExe = Join-Path $InstallDir 'MaterialClassification_CLI.exe'
    if (-not (Test-Path -LiteralPath $cliExe)) {
        Fail ("entry executable missing after install: {0}" -f $cliExe)
    }
    # (a) Entry path: MaterialClassification_CLI.exe --examples exits 0 without
    #     importing torch (cli.py defers all heavy imports into functions), so
    #     this cheaply proves the exe -> cli_launcher -> venv -> cli.py chain.
    Invoke-Native -Exe $cliExe -Arguments @('--examples') -What 'CLI entry (--examples)' | Out-Null

    # (b) Heavy stack: the managed task needs torch + backend.app.core to
    #     import. sitecustomize guards the OpenMP segfault; run from the
    #     install root so backend.app.* resolves.
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
catch {
    Fail ("Unhandled error: {0}`n{1}" -f $_.Exception.Message, $_.ScriptStackTrace)
}
