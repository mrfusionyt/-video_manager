@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

pushd "%~dp0"
echo ===================================================
echo  Starting Video Manager
echo ===================================================
echo.

REM --- 1. Find Python ---------------------------------------------
set "PY_CMD="

python --version >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    set "PY_CMD=python"
    goto :found_python
)

python3 --version >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    set "PY_CMD=python3"
    goto :found_python
)

py --version >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    set "PY_CMD=py"
    goto :found_python
)

if exist "C:\Program Files\Python312\python.exe" (
    set "PY_CMD=C:\Program Files\Python312\python.exe"
    goto :found_python
)
if exist "C:\Program Files\Python313\python.exe" (
    set "PY_CMD=C:\Program Files\Python313\python.exe"
    goto :found_python
)
if exist "C:\Program Files\Python314\python.exe" (
    set "PY_CMD=C:\Program Files\Python314\python.exe"
    goto :found_python
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    goto :found_python
)
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
    set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    goto :found_python
)
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
    set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    goto :found_python
)

echo [ERROR] Python not found.
echo Please install Python 3.10+ and add it to PATH.
pause
popd
exit /b 1

:found_python
for /f "tokens=2" %%v in ('%PY_CMD% --version 2^>^&1') do set PYVER=%%v
echo [*] Found Python !PYVER! (!PY_CMD!)

REM --- 2. venv ----------------------------------------------------
if not exist "venv\Scripts\python.exe" (
    echo [*] Virtual environment not found. Creating...
    "%PY_CMD%" -m venv venv
    if !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Failed to create venv.
        pause
        popd
        exit /b 1
    )
    echo [*] venv created.
) else (
    echo [*] Using existing venv.
)

set PY=venv\Scripts\python.exe

REM --- 3. Dependencies --------------------------------------------
echo.
echo [*] Checking dependencies...
"%PY%" -m pip install --upgrade pip --quiet
"%PY%" -m pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    popd
    exit /b 1
)

REM --- 4. Run -----------------------------------------------------
echo.
echo ===================================================
echo  Server is running: http://127.0.0.1:5000
echo  Close -- Ctrl+C in this window.
echo ===================================================
echo.

"%PY%" app.py

pause
popd
endlocal