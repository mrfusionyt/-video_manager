@echo off
setlocal
chcp 65001 >nul

set "HERE=%~dp0"
cd /d "%HERE%"

echo ===================================================
echo  VideoManager build (PyInstaller onedir)
echo  Folder: %HERE%
echo ===================================================
echo.

set "PYTHON=python"
if exist "%HERE%venv\Scripts\python.exe" set "PYTHON=%HERE%venv\Scripts\python.exe"

"%PYTHON%" --version
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    pause
    exit /b 1
)
echo.

echo [*] Installing dependencies...
"%PYTHON%" -m pip install --disable-pip-version-check -r "%HERE%requirements.txt"
if errorlevel 1 (
    echo [ERROR] Could not install requirements.
    pause
    exit /b 1
)
echo.

echo [*] Cleaning previous build...
if exist "%HERE%build" rmdir /s /q "%HERE%build"
if exist "%HERE%dist" rmdir /s /q "%HERE%dist"
echo.

echo [*] PyInstaller (onedir, launcher entry point)...
"%PYTHON%" -m PyInstaller --noconfirm --clean ^
    --distpath "%HERE%dist" ^
    --workpath "%HERE%build" ^
    "%HERE%VideoManager.spec"
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo ===================================================
echo  BUILD OK
echo  EXE: %HERE%dist\VideoManager\VideoManager.exe
echo ===================================================
echo.
echo Next step:  python make_release.py
pause
endlocal
