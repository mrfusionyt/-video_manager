@echo off
echo === Build Video Manager (onedir) ===

set VENV_PY=%~dp0venv\Scripts\python.exe

if not exist "%VENV_PY%" (
    echo ERROR: venv python not found at %VENV_PY%
    echo Make sure the venv folder exists next to this build.bat.
    pause
    exit /b 1
)

echo Using Python: %VENV_PY%
"%VENV_PY%" -c "import sys; print('  sys.executable =', sys.executable)"
"%VENV_PY%" -c "import instagrapi; print('  instagrapi =', instagrapi.__file__)"

echo Installing / upgrading PyInstaller into venv...
"%VENV_PY%" -m pip install --upgrade pyinstaller

echo Cleaning old build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo Building...
"%VENV_PY%" -m PyInstaller --onedir --console ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --add-data "image;image" ^
    --add-data "addon;addon" ^
    --collect-all instagrapi ^
    --collect-all yt_dlp ^
    --collect-all cv2 ^
    --collect-all imagehash ^
    --collect-all playwright ^
    --name "VideoManager" app.py

echo === Done ===
pause