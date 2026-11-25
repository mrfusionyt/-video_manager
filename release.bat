@echo off
rem ===================================================================
rem  VideoManager - ONE COMMAND RELEASE (build in the cloud)
rem
rem    release.bat                 current version -> tag -> GitHub Actions
rem    release.bat 1.0.1           bump version, tag, push, build in the cloud
rem    release.bat --watch         ... and wait for the Actions run
rem    release.bat --dry-run       preview only, change nothing
rem    release.bat 1.0.1 --force-tag   replace an existing tag
rem
rem  GitHub Actions builds the exe, packs the zip and publishes the
rem  release. Nothing is compiled on this PC.
rem ===================================================================
setlocal DisableDelayedExpansion
chcp 65001 >nul
title VideoManager - cloud release
cd /d "%~dp0"

set "PYTHON=%~dp0venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python not found. Needed only to edit the version and push the tag.
    echo         Install Python 3.10+ or create a venv, then run this file again.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting the cloud release pipeline...
echo.

"%PYTHON%" "%~dp0tools\cloud_release.py" %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo ===================================================
    echo  TAG PUSHED - GITHUB IS BUILDING THE RELEASE
    echo  Look above for the Actions and release links.
    echo ===================================================
) else (
    echo ===================================================
    echo  FINISHED WITH AN ERROR  ^(code %RC%^)
    echo  See cloud_release.log and the messages above.
    echo ===================================================
)
echo.
pause
endlocal
exit /b %RC%
