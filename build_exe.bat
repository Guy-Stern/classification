@echo off
cd /d "%~dp0"

echo ============================================
echo Building ClassificationWebApp + CLI (standalone)
echo ============================================
echo.

REM ── Find Python ──────────────────────────────────────────────────────────
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
    echo Using venv Python: %PYTHON%
) else (
    set PYTHON=python
    echo Using system Python
)

REM ── Step 1: Build the Vite frontend ──────────────────────────────────────
echo.
echo [1/3] Building frontend (Vite)...
cd web_app
call npm run build
if errorlevel 1 (
    echo ERROR: npm run build failed
    cd ..
    pause
    exit /b 1
)
cd ..
echo Frontend built to web_app\dist\

REM ── Step 2: Build the GUI launcher exe ───────────────────────────────────
echo.
echo [2/3] Building GUI launcher exe (PyInstaller)...
if exist "build" rmdir /s /q build

"%PYTHON%" -m PyInstaller ^
    --onefile ^
    --name ClassificationWebApp ^
    --distpath . ^
    --workpath build ^
    --noconfirm ^
    --clean ^
    launcher.py
if errorlevel 1 (
    echo ERROR: GUI launcher build failed
    pause
    exit /b 1
)

REM ── Step 3: Build the CLI launcher exe ───────────────────────────────────
echo.
echo [3/3] Building CLI launcher exe (PyInstaller)...

"%PYTHON%" -m PyInstaller ^
    --distpath . ^
    --workpath build_cli ^
    --noconfirm ^
    --clean ^
    MaterialClassification_CLI.spec
if errorlevel 1 (
    echo ERROR: CLI launcher build failed
    pause
    exit /b 1
)

echo.
if exist "ClassificationWebApp.exe" if exist "MaterialClassification_CLI.exe" (
    echo ============================================
    echo BUILD SUCCESSFUL
    echo ============================================
    echo.
    echo GUI launcher:   ClassificationWebApp.exe        (project root)
    echo CLI launcher:   MaterialClassification_CLI.exe  (project root)
    echo Frontend files: web_app\dist\
    echo.
    echo To deploy, copy this entire folder to the target machine.
    echo Both .venv\ and models\hf_cache\ must be present.
    echo.
    echo Run GUI: ClassificationWebApp.exe
    echo Run CLI: MaterialClassification_CLI.exe --input photo.tif --mea
) else (
    echo ============================================
    echo BUILD FAILED - check output above
    echo ============================================
)

echo.
pause
