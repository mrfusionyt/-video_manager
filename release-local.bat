@echo off
rem ===================================================================
rem  VideoManager - LOCAL release (fallback)
rem  Builds the exe on this PC, packs the zip and uploads it via gh/token.
rem  Prefer release.bat: it builds on GitHub instead.
rem
rem    release-local.bat                 build + publish current version
rem    release-local.bat 1.0.1           bump version, build, publish
rem    release-local.bat --zip-only      build + zip, no GitHub
rem    release-local.bat --skip-build    repack the existing dist only
rem ===================================================================
setlocal DisableDelayedExpansion
chcp 65001 >nul
title VideoManager - local release
cd /d "%~dp0"

set "PYTHON=%~dp0venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python not found. Install Python 3.10+ or create venv.
    echo.
    pause
    exit /b 1
)

"%PYTHON%" "%~dp0tools\release_cli.py" %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo ===================================================
    echo  DONE
    echo ===================================================
) else (
    echo ===================================================
    echo  FINISHED WITH WARNINGS  ^(code %RC%^)
    echo  Look at release.log and the messages above.
    echo ===================================================
)
echo.
pause
endlocal
exit /b %RC%
