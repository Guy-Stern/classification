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
if (-not $LauncherCs) { $LauncherCs = Join-Path $scriptDir 'SilentSetupLauncher.cs' }
$OfflineDir = [System.IO.Path]::GetFullPath($OfflineDir)
$AssetsDir  = [System.IO.Path]::GetFullPath($AssetsDir)
$OutFile    = [System.IO.Path]::GetFullPath($OutFile)
$BuildTools = [System.IO.Path]::GetFullPath($BuildTools)
if (-not $StagingDir) { $StagingDir = Join-Path $BuildTools 'silent_payload' }
New-Item -ItemType Directory -Path $BuildTools -Force | Out-Null

Add-Type -AssemblyName System.IO.Compression | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null

$TRAILER_MAGIC = [System.Text.Encoding]::ASCII.GetBytes('MCSFX001')  # matches SilentSetupLauncher.cs

# -- Compile the C# launcher to a standalone console exe ----------------------
function Compile-Launcher([string]$csFile, [string]$outExe) {
    if (-not (Test-Path -LiteralPath $csFile)) { Die "Launcher source not found: $csFile" }
    $provider = New-Object Microsoft.CSharp.CSharpCodeProvider
    $params = New-Object System.CodeDom.Compiler.CompilerParameters
    $params.GenerateExecutable = $true
    $params.OutputAssembly = $outExe
    $params.CompilerOptions = '/target:exe /platform:x64 /optimize'
    foreach ($ref in @('System.dll', 'System.IO.Compression.dll', 'System.IO.Compression.FileSystem.dll')) {
        [void]$params.ReferencedAssemblies.Add($ref)
    }
    if (Test-Path -LiteralPath $outExe) { Remove-Item -LiteralPath $outExe -Force }
    $src = [System.IO.File]::ReadAllText($csFile)
    $res = $provider.CompileAssemblyFromSource($params, $src)
    if ($res.Errors.HasErrors) {
        foreach ($e in $res.Errors) { Write-Host ("  " + $e.ToString()) -ForegroundColor Red }
        Die 'Launcher compilation failed.'
    }
    if (-not (Test-Path -LiteralPath $outExe)) { Die 'Launcher exe not produced.' }
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
$launcherExe = Join-Path $BuildTools 'SilentSetupLauncher.exe'
Info "Compiling launcher: $LauncherCs"
Compile-Launcher $LauncherCs $launcherExe

if (-not $SkipSelfTest) { Invoke-SelfTest $launcherExe }
else { Info 'Self-test SKIPPED (-SkipSelfTest). Exit-code propagation is UNVERIFIED.' }

if ($SelfTestOnly) { Info 'Self-test only - stopping before payload build.'; exit 0 }

# -- Sanity-check the offline payload -----------------------------------------
Info "offline payload: $OfflineDir"
foreach ($need in @('app', 'offline_packages', 'app\requirements.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $OfflineDir $need))) {
        Die "offline_installer is incomplete (missing '$need'). Run prepare_offline.bat first."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $OfflineDir 'prerequisites\python311\python.exe'))) {
    Info 'WARNING: prerequisites\python311\python.exe not in payload; the silent'
    Info '         installer will fall back to a system Python 3.11 on the target.'
}

# -- Stage the payload --------------------------------------------------------
Info "Staging payload -> $StagingDir"
if (Test-Path -LiteralPath $StagingDir) { Remove-Item -LiteralPath $StagingDir -Recurse -Force }
New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

# offline_installer/* (app, offline_packages*, prerequisites) - the install source.
Get-ChildItem -LiteralPath $OfflineDir -Force |
    Where-Object { $_.Name -notin @('Setup.ps1', 'Setup.bat', 'README.md', 'README.txt', 'STANDALONE_DEPLOYMENT.md') } |
    ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $StagingDir $_.Name) -Recurse -Force
    }

# assets/ - the Post-Install extras the silent installer folds in.
$stagedAssets = Join-Path $StagingDir 'assets'
New-Item -ItemType Directory -Path $stagedAssets -Force | Out-Null
foreach ($a in @('sam3_runtime', 'examples')) {
    $s = Join-Path $AssetsDir $a
    if (Test-Path -LiteralPath $s) { Copy-Item -LiteralPath $s -Destination (Join-Path $stagedAssets $a) -Recurse -Force }
}
$shpEx = Join-Path $AssetsDir 'shapefile_config.example.json'
if (Test-Path -LiteralPath $shpEx) { Copy-Item -LiteralPath $shpEx -Destination $stagedAssets -Force }

# The installer script at the payload root (the launcher runs it directly).
Copy-Item -LiteralPath (Join-Path $scriptDir 'silent_install.ps1') -Destination $StagingDir -Force

# -- Build the single exe -----------------------------------------------------
$zipPath = Join-Path $BuildTools 'silent_payload.zip'
Info 'Building payload ZIP (this can take a while for a multi-GB payload)...'
Build-PayloadZip $StagingDir $zipPath
Info ("payload.zip = {0} GB" -f [math]::Round((Get-Item $zipPath).Length / 1GB, 2))
Info "Assembling $OutFile"
New-Overlay $launcherExe $zipPath $OutFile

Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
if (-not $KeepStaging) { Remove-Item -LiteralPath $StagingDir -Recurse -Force -ErrorAction SilentlyContinue }

$sizeGB = [math]::Round((Get-Item -LiteralPath $OutFile).Length / 1GB, 2)
Info "DONE: $OutFile  (${sizeGB} GB)"
Write-Output $OutFile
