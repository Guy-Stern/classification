<#
    build_silent_installer.ps1

    Packages the offline payload + the headless silent_install.ps1 into ONE
    self-extracting exe: MaterialClassification_Silent_Setup.exe.

    Mechanism (see SilentSetupLauncher.cs for the why): a small C# launcher is
    compiled in-box (no external SDK), then the payload ZIP + a 16-byte trailer
    are appended to it:

        [ launcher.exe ][ payload.zip ][ int64 zipLength ][ magic "MCSFX001" ]

    JARVIS runs `MaterialClassification_Silent_Setup.exe -InstallDir {INSTALL_DIR}`.
    The launcher parses -InstallDir itself, extracts the ZIP to a scratch temp
    dir, runs silent_install.ps1 -InstallDir <dir>, and returns that script's
    exit code -> clauses (a) silent, (b) redirectable, (e) honest exit code.

    A fast SELF-TEST runs first (unless -SkipSelfTest): it builds a tiny
    installer whose inner script exits 42 and echoes its -InstallDir, runs it,
    and asserts the exe returns 42 AND the install dir was forwarded. If either
    fails the build ABORTS.

    Requirements on the BUILD machine:
      * A completed offline_installer\ tree (run prepare_offline.bat first).
      * .NET Framework (csc via CodeDom) + PowerShell 5.1. No downloads, no admin.
#>
[CmdletBinding()]
param(
    [string]$OfflineDir,
    [string]$AssetsDir,
    [string]$OutFile,
    [string]$BuildTools,
    [string]$LauncherCs,
    [string]$StagingDir,
    [switch]$SkipSelfTest,
    [switch]$SelfTestOnly,
    [switch]$KeepStaging
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Info($m) { Write-Host "[build-silent] $m" }
function Die($m)  { Write-Host "[build-silent] ERROR: $m" -ForegroundColor Red; exit 1 }

# Resolve paths in the body ($PSScriptRoot is unreliable in param defaults).
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDir) { $scriptDir = $PSScriptRoot }
$root = (Resolve-Path (Join-Path $scriptDir '..')).Path
if (-not $OfflineDir) { $OfflineDir = Join-Path $root 'offline_installer' }
if (-not $AssetsDir)  { $AssetsDir  = Join-Path $root 'installer_assets' }
if (-not $OutFile)    { $OutFile    = Join-Path $root 'MaterialClassification_Silent_Setup.exe' }
if (-not $BuildTools) { $BuildTools = Join-Path $root '_build_tools' }
$LauncherC = Join-Path $scriptDir 'native_launcher.c'
$ExtractPs = Join-Path $scriptDir 'silent_extract.ps1'
$OfflineDir = [System.IO.Path]::GetFullPath($OfflineDir)
$AssetsDir  = [System.IO.Path]::GetFullPath($AssetsDir)
$OutFile    = [System.IO.Path]::GetFullPath($OutFile)
$BuildTools = [System.IO.Path]::GetFullPath($BuildTools)
if (-not $StagingDir) { $StagingDir = Join-Path $BuildTools 'silent_payload' }
New-Item -ItemType Directory -Path $BuildTools -Force | Out-Null

Add-Type -AssemblyName System.IO.Compression | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null

$TRAILER_MAGIC = [System.Text.Encoding]::ASCII.GetBytes('MCSFX001')  # matches native_launcher.c

# -- Locate the MSVC toolchain (vcvars64.bat) ---------------------------------
function Find-VcVars {
    $roots = @("${env:ProgramFiles}\Microsoft Visual Studio",
               "${env:ProgramFiles(x86)}\Microsoft Visual Studio")
    foreach ($r in $roots) {
        if (-not (Test-Path -LiteralPath $r)) { continue }
        $hit = Get-ChildItem -LiteralPath $r -Recurse -Filter 'vcvars64.bat' -ErrorAction SilentlyContinue |
               Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    Die ('Could not find vcvars64.bat (MSVC Build Tools). Install "Desktop development ' +
         'with C++" or set the toolchain up, then re-run.')
}

# -- Compile the NATIVE launcher stub, with silent_extract.ps1 base64-embedded.
#    Native (not managed) so Windows can LAUNCH the >4 GB single-file exe. -----
function Compile-NativeLauncher([string]$outExe) {
    if (-not (Test-Path -LiteralPath $LauncherC)) { Die "Launcher source not found: $LauncherC" }
    if (-not (Test-Path -LiteralPath $ExtractPs)) { Die "Extract script not found: $ExtractPs" }

    # Embed silent_extract.ps1 as a UTF-16 base64 string for `powershell -EncodedCommand`.
    $psText = [System.IO.File]::ReadAllText($ExtractPs)
    $enc = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($psText))
    $cSrc = ([System.IO.File]::ReadAllText($LauncherC)).Replace('@@ENC@@', $enc)
    $cGen = Join-Path $BuildTools 'native_launcher.gen.c'
    [System.IO.File]::WriteAllText($cGen, $cSrc, (New-Object System.Text.ASCIIEncoding))

    $vcvars = Find-VcVars
    Info "MSVC: $vcvars"
    if (Test-Path -LiteralPath $outExe) { Remove-Item -LiteralPath $outExe -Force }
    $bat = Join-Path $BuildTools 'compile_native.bat'
    $batText = '@echo off' + "`r`n" +
               ('call "{0}" >nul' -f $vcvars) + "`r`n" +
               ('cd /d "{0}"' -f $BuildTools) + "`r`n" +
               ('cl /nologo /O1 /DUNICODE /D_UNICODE "{0}" /Fe:"{1}" /link kernel32.lib' -f $cGen, $outExe) + "`r`n" +
               'exit /b %ERRORLEVEL%' + "`r`n"
    [System.IO.File]::WriteAllText($bat, $batText, (New-Object System.Text.ASCIIEncoding))
    & cmd /c "`"$bat`""
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $outExe)) {
        Die "native launcher compile failed (cl exit $LASTEXITCODE)."
    }
}

# -- Build a ZIP of a directory (stored, no compression: payload bytes are
#    already compressed, and NoCompression keeps the build to a disk copy) ----
function Build-PayloadZip([string]$srcDir, [string]$zipPath) {
    if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    $full = (Resolve-Path -LiteralPath $srcDir).Path
    $prefixLen = $full.Length + 1
    $fs = [System.IO.File]::Create($zipPath)
    try {
        $zip = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Create)
        try {
            foreach ($f in (Get-ChildItem -LiteralPath $full -Recurse -File -Force)) {
                $rel = $f.FullName.Substring($prefixLen).Replace('\', '/')
                $entry = $zip.CreateEntry($rel, [System.IO.Compression.CompressionLevel]::NoCompression)
                $es = $entry.Open()
                try {
                    $ins = [System.IO.File]::OpenRead($f.FullName)
                    try { $ins.CopyTo($es) } finally { $ins.Dispose() }
                }
                finally { $es.Dispose() }
            }
        }
        finally { $zip.Dispose() }
    }
    finally { $fs.Dispose() }
}

# -- Concatenate launcher + zip + trailer -> single exe -----------------------
function New-Overlay([string]$launcherExe, [string]$zipPath, [string]$outExe) {
    $zipLen = (Get-Item -LiteralPath $zipPath).Length
    if (Test-Path -LiteralPath $outExe) { Remove-Item -LiteralPath $outExe -Force }
    $out = [System.IO.File]::Create($outExe)
    try {
        foreach ($p in @($launcherExe, $zipPath)) {
            $s = [System.IO.File]::OpenRead($p)
            try { $s.CopyTo($out) } finally { $s.Dispose() }
        }
        $out.Write([System.BitConverter]::GetBytes([int64]$zipLen), 0, 8)
        $out.Write($TRAILER_MAGIC, 0, 8)
    }
    finally { $out.Dispose() }
}

# -- SELF-TEST: prove -InstallDir is parsed and the exit code is returned ------
function Invoke-SelfTest([string]$launcherExe) {
    Info 'Self-test: verifying -InstallDir forwarding + exit-code propagation...'
    $stRoot = Join-Path $BuildTools 'silent_selftest'
    if (Test-Path -LiteralPath $stRoot) { Remove-Item -LiteralPath $stRoot -Recurse -Force }
    New-Item -ItemType Directory -Path $stRoot -Force | Out-Null

    # Dummy inner installer: record the -InstallDir it received, exit 42.
    $dummy = @'
param([string]$InstallDir, [string]$PayloadDir)
$sink = $env:MC_SELFTEST_SINK
if ($sink) { Set-Content -LiteralPath $sink -Value ("INSTALLDIR=" + $InstallDir) -Encoding ASCII }
exit 42
'@
    Set-Content -LiteralPath (Join-Path $stRoot 'silent_install.ps1') -Value $dummy -Encoding ASCII

    $zip = Join-Path $BuildTools 'selftest.zip'
    Build-PayloadZip $stRoot $zip
    $exe = Join-Path $BuildTools 'silent_selftest.exe'
    New-Overlay $launcherExe $zip $exe

    $sink = Join-Path $BuildTools 'selftest_sink.txt'
    if (Test-Path -LiteralPath $sink) { Remove-Item -LiteralPath $sink -Force }
    $sentinel = 'C:\__mc_probe__\v1'
    $env:MC_SELFTEST_SINK = $sink
    & $exe '-InstallDir' $sentinel | Out-Null
    $rc = $LASTEXITCODE
    $env:MC_SELFTEST_SINK = $null

    $forwarded = (Test-Path -LiteralPath $sink) -and
                 ((Get-Content -LiteralPath $sink -Raw) -match [regex]::Escape($sentinel))

    Remove-Item -LiteralPath $zip, $exe, $sink -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stRoot -Recurse -Force -ErrorAction SilentlyContinue

    if ($rc -ne 42) {
        Die ("Launcher did NOT return the inner exit code (got $rc, expected 42). " +
             "It would report success even when an install fails, violating clause (e).")
    }
    if (-not $forwarded) {
        Die ("Launcher did NOT forward -InstallDir to the inner installer (clause (b)).")
    }
    Info 'Self-test PASSED: exit code returned (42) and -InstallDir forwarded.'
}

# -- Run ----------------------------------------------------------------------
$launcherExe = Join-Path $BuildTools 'native_launcher.exe'
Info "Compiling native launcher: $LauncherC"
Compile-NativeLauncher $launcherExe

if (-not $SkipSelfTest) { Invoke-SelfTest $launcherExe }
else { Info 'Self-test SKIPPED (-SkipSelfTest). Exit-code propagation is UNVERIFIED.' }

if ($SelfTestOnly) { Info 'Self-test only - stopping before payload build.'; exit 0 }

# -- Sanity-check the offline payload -----------------------------------------
# LEAN installer: bundles Python + app CODE only. The heavy, version-stable data
# (wheels + weights) is NOT bundled - it lives in the per-box shared dir, so the
# installer stays well under the 4 GB Windows exe launch limit.
Info "offline payload: $OfflineDir"
foreach ($need in @('app', 'app\requirements.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $OfflineDir $need))) {
        Die "offline_installer is incomplete (missing '$need')."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $OfflineDir 'prerequisites\python311\python.exe'))) {
    Info 'WARNING: prerequisites\python311\python.exe not in payload; the silent'
    Info '         installer will fall back to a system Python 3.11 on the target.'
}

# -- Stage the LEAN payload ---------------------------------------------------
Info "Staging payload -> $StagingDir"
if (Test-Path -LiteralPath $StagingDir) { Remove-Item -LiteralPath $StagingDir -Recurse -Force }
New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

# app\ code, WITHOUT models\ (weights are shared, not per-version).
$stagedApp = Join-Path $StagingDir 'app'
New-Item -ItemType Directory -Path $stagedApp -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $OfflineDir 'app') -Force |
    Where-Object { $_.Name -ne 'models' } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $stagedApp $_.Name) -Recurse -Force }

# Full venv-capable Python 3.11 (small).
$prereq = Join-Path $OfflineDir 'prerequisites'
if (Test-Path -LiteralPath $prereq) {
    Copy-Item -LiteralPath $prereq -Destination (Join-Path $StagingDir 'prerequisites') -Recurse -Force
}

# assets/ - examples + shapefile example (+ BPE as a small fallback; normally shared).
$stagedAssets = Join-Path $StagingDir 'assets'
New-Item -ItemType Directory -Path $stagedAssets -Force | Out-Null
foreach ($a in @('sam3_runtime', 'examples')) {
    $s = Join-Path $AssetsDir $a
    if (Test-Path -LiteralPath $s) { Copy-Item -LiteralPath $s -Destination (Join-Path $stagedAssets $a) -Recurse -Force }
}
$shpEx = Join-Path $AssetsDir 'shapefile_config.example.json'
if (Test-Path -LiteralPath $shpEx) { Copy-Item -LiteralPath $shpEx -Destination $stagedAssets -Force }

# The installer script at the payload root.
Copy-Item -LiteralPath (Join-Path $scriptDir 'silent_install.ps1') -Destination $StagingDir -Force

# -- Build the single exe -----------------------------------------------------
$zipPath = Join-Path $BuildTools 'silent_payload.zip'
Info 'Building payload ZIP...'
Build-PayloadZip $StagingDir $zipPath
$zipGB = [math]::Round((Get-Item $zipPath).Length / 1GB, 2)
Info ("payload.zip = {0} GB" -f $zipGB)
if ((Get-Item $zipPath).Length -ge 3.9GB) {
    Die ("payload is {0} GB - too close to the 4 GB Windows exe launch limit. " +
         "Ensure models\ and wheels are excluded (shared-data model)." -f $zipGB)
}
Info "Assembling $OutFile"
New-Overlay $launcherExe $zipPath $OutFile

Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
if (-not $KeepStaging) { Remove-Item -LiteralPath $StagingDir -Recurse -Force -ErrorAction SilentlyContinue }

$sizeGB = [math]::Round((Get-Item -LiteralPath $OutFile).Length / 1GB, 2)
Info "DONE: $OutFile  (${sizeGB} GB)"
Write-Output $OutFile
