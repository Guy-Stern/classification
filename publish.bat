@echo off
REM ======================================================================
REM  publish.bat — publish a version (interactive + silent installers + recipe)
REM
REM  Thin launcher for publish.ps1. Pass any publish.ps1 switches straight
REM  through, e.g.:
REM      publish.bat
REM      publish.bat -SkipExeBuild -SkipPrepareOffline
REM      publish.bat -Force
REM ======================================================================
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish.ps1" %*
exit /b %ERRORLEVEL%
