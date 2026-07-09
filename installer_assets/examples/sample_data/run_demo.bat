@echo off
rem ============================================================
rem  Layered JPEG 2000 + GeoTIFF manifest demo.
rem  Double-click to run. Classifies the two sample layers into
rem  one geocell (N33E035) and writes out\demo_material.tif + .xml.
rem ============================================================
cd /d "%~dp0"

rem Guard against the "multiple OpenMP runtimes" crash (exit 139) seen on some
rem Windows boxes where faiss / numpy-MKL / torch each load their own libiomp.
rem Harmless on machines that don't have the conflict.
set KMP_DUPLICATE_LIB_OK=TRUE
set OMP_NUM_THREADS=1

set "CLI=%~dp0..\..\MaterialClassification_CLI.exe"
if not exist "%CLI%" (
    echo Could not find MaterialClassification_CLI.exe two folders up.
    echo Run this from  ^<install^>\examples\sample_data\ , or run the CLI
    echo manually:  MaterialClassification_CLI.exe --manifest "%~dp0geocell.toml"
    pause
    exit /b 1
)

echo Running the layered JP2 + TIFF manifest demo...
echo   manifest: %~dp0geocell.toml
echo   output:   %~dp0out\demo_material.tif
echo.
"%CLI%" --manifest "%~dp0geocell.toml"

echo.
echo Done. See  out\demo_material.tif  and  out\demo_material.xml  in this folder.
pause
