@echo off
:: ======================================================================
::  Post-Install patch for ClassificationInstaller.exe
::
::  The bundled .NET wrapper exe was compiled before this branch added:
::    - cli.py / cli_launcher.py / *.exe runtime launchers
::    - SAM3 road/building extraction (needs torch + sam3 + timm)
::    - shapefile_resolver mechanism
::
::  This script idempotently fixes all of that:
::    1. Copies the four files the wrapper missed
::    2. Installs the AI deps the wrapper didn't install (torch, sam3,
::       timm, segment-geospatial) from the offline wheel cache
::    3. Drops a shapefile_config.json template
::
::  Run this AFTER ClassificationInstaller.exe finishes.
:: ======================================================================
setlocal
cd /d "%~dp0"
title Classification - Post-Install Patch

set "DEFAULT_DIR=C:\ClassificationApp"
set "INSTALL_DIR=%DEFAULT_DIR%"
set /p "INSTALL_DIR=Install directory [default %DEFAULT_DIR%]: "
if "%INSTALL_DIR%"=="" set "INSTALL_DIR=%DEFAULT_DIR%"

set "VPY=%INSTALL_DIR%\.venv\Scripts\python.exe"
if not exist "%VPY%" (
    echo.
    echo ERROR: %INSTALL_DIR% does not look like a finished install.
    echo        Missing: .venv\Scripts\python.exe
    echo.
    echo Run ClassificationInstaller.exe first, then re-run this script.
    pause
    exit /b 1
)

set "SRC=%~dp0offline_installer"
if not exist "%SRC%\app\cli.py" (
    echo.
    echo ERROR: %SRC%\app\cli.py not found.
    echo        Run this from the same folder that contains the
    echo        offline_installer\ subfolder.
    pause
    exit /b 1
)

:: ── [1/3] Copy CLI files the wrapper doesn't know about ───────────────
echo.
echo [1/3] Copying CLI files into %INSTALL_DIR% ...
for %%F in (cli.py cli_launcher.py sde_conn_test.py ClassificationWebApp.exe MaterialClassification_CLI.exe) do (
    if exist "%SRC%\app\%%F" (
        copy /y "%SRC%\app\%%F" "%INSTALL_DIR%\%%F" >nul
        echo   copied  %%F
    )
)

:: ── [1b/3] Copy usage examples (USE_CASES.txt + geocell template) ──────
echo.
echo [1b/3] Copying usage examples...
if exist "%~dp0examples" (
    xcopy /e /i /y /q "%~dp0examples" "%INSTALL_DIR%\examples\" >nul
    echo   copied  examples\  - see examples\USE_CASES.txt
) else (
    echo   NOTE: examples\ folder not found next to this script - skipping.
)

:: ── [2/3] Install AI deps the wrapper missed ──────────────────────────
:: torch + torchvision come from offline_packages_torch (cu121 wheels —
:: they work fine on the A4000's CUDA 12.4 driver via forward-compat).
:: sam3 + timm + segment-geospatial come from offline_packages.
:: Idempotent: pip skips packages that are already installed.
echo.
echo [2/3] Installing AI dependencies (this may take a few minutes)...
echo       Source: %SRC%\offline_packages*

:: Strictly the wheels SAM3 priority-0 path needs. Skipping
:: segment-geospatial — it's only used by the SamGeo3 / LangSAM
:: fallback paths, which never fire when sam3.1_multiplex.pt is
:: present. Bundling it pulled in 22+ transitive deps (matplotlib,
:: gdown, segment_anything, ...) that slowed SAM3 inference ~20x
:: in testing.
"%VPY%" -m pip install --no-index ^
    --find-links "%SRC%\offline_packages" ^
    --find-links "%SRC%\offline_packages_torch" ^
    --find-links "%SRC%\offline_packages_gpu" ^
    torch torchvision sam3 timm triton-windows
if errorlevel 1 (
    echo.
    echo WARNING: pip install reported errors. The CLI may still work for
    echo          KMeans-only mode, but SAM3 road/building extraction
    echo          requires all of torch / sam3 / timm / triton-windows
    echo          to install cleanly. Try running this script again, or
    echo          check the messages above.
    echo.
)

:: ── [2b/3] Drop the SAM3 BPE tokenizer asset ──────────────────────────
:: The sam3 wheel ships without its own assets/ directory, but its
:: tokenizer looks for bpe_simple_vocab_16e6.txt.gz at
:: <site-packages>/assets/. Without it, SAM3 fails to load and the
:: pipeline silently falls back to OWLv2+SAM2 (much slower).
echo.
echo [2b/3] Installing SAM3 BPE tokenizer asset...
set "ASSETS_DIR=%INSTALL_DIR%\.venv\Lib\site-packages\assets"
if not exist "%ASSETS_DIR%" mkdir "%ASSETS_DIR%" >nul 2>&1
if exist "%~dp0sam3_runtime\bpe_simple_vocab_16e6.txt.gz" (
    copy /y "%~dp0sam3_runtime\bpe_simple_vocab_16e6.txt.gz" "%ASSETS_DIR%\" >nul
    echo   copied  bpe_simple_vocab_16e6.txt.gz -> %ASSETS_DIR%
) else (
    echo   WARNING: sam3_runtime\bpe_simple_vocab_16e6.txt.gz not found.
    echo            SAM3 will fall back to OWLv2+SAM2 ^(~10x slower^).
)

:: ── [3/3] Drop shapefile_config.json template ─────────────────────────
echo.
echo [3/3] Ensuring shapefile_config.json exists at install root...
if not exist "%INSTALL_DIR%\shapefile_config.json" (
    > "%INSTALL_DIR%\shapefile_config.json" echo {
    >>"%INSTALL_DIR%\shapefile_config.json" echo   "water_mask": "",
    >>"%INSTALL_DIR%\shapefile_config.json" echo   "sde": {
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "enabled": true,
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "connection_file": "SET_ME_path_to_your_connection.sde",
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "arcpy_python": "C:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe",
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "tile_size_metres": 5000,
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "timeout_seconds": 1800,
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "road_width_attr": "",
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "Road_Type_Attr": "",
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "Road_Type_Key_MainRoad": "",
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "Road_Type_Width_MainRoad_m": 0.0,
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "Road_Type_Key_SideRoad": "",
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "Road_Type_Width_SideRoad_m": 0.0,
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "road_width_fallback_m": 2.0,
    >>"%INSTALL_DIR%\shapefile_config.json" echo     "layers": {
    >>"%INSTALL_DIR%\shapefile_config.json" echo       "buildings": "SET_ME_GDB.SCHEMA.BUILDINGS",
    >>"%INSTALL_DIR%\shapefile_config.json" echo       "roads": "SET_ME_GDB.SCHEMA.ROADS"
    >>"%INSTALL_DIR%\shapefile_config.json" echo     }
    >>"%INSTALL_DIR%\shapefile_config.json" echo   }
    >>"%INSTALL_DIR%\shapefile_config.json" echo }
    echo   created  shapefile_config.json  ^(with sde block — edit connection_file + layers - see README^)
) else (
    echo   already exists, leaving alone.
)

:: ── [3b/3] Drop a filled-in example config next to the live one ────────
:: A complete, realistic shapefile_config.json (all SDE + road-width tiers
:: populated) the operator can copy values from. Never clobbers the live
:: config — it lands under a distinct .example.json name.
if exist "%~dp0shapefile_config.example.json" (
    copy /y "%~dp0shapefile_config.example.json" "%INSTALL_DIR%\shapefile_config.example.json" >nul
    echo   copied  shapefile_config.example.json  ^(reference — see README^)
)

echo.
echo ======================================================================
echo  DONE — patched %INSTALL_DIR%.
echo ======================================================================
echo.
echo Try the CLI:
echo   "%INSTALL_DIR%\MaterialClassification_CLI.exe" --examples
echo.
echo Usage examples and a ready-to-edit manifest template:
echo   "%INSTALL_DIR%\examples\USE_CASES.txt"
echo   "%INSTALL_DIR%\examples\geocell.example.toml"
echo.
echo Configure SDE / shapefile paths - edit the "sde" block:
echo   notepad "%INSTALL_DIR%\shapefile_config.json"
echo   ^(set connection_file + layers; see README.txt SDE section^)
echo.
echo Test the SDE connection ^(writes a .log you can send back^):
echo   "%INSTALL_DIR%\.venv\Scripts\python.exe" "%INSTALL_DIR%\sde_conn_test.py"
echo.
pause
endlocal
