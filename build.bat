@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ===================================================
echo Сборка Video Manager в EXE (режим onedir)
echo ===================================================
echo.

cd /d "%~dp0"
echo [*] Текущая директория: %cd%
echo.

set PYTHON=python
if exist "venv\Scripts\python.exe" (
    echo [*] Найдено venv, использую его.
    set PYTHON=venv\Scripts\python.exe
    call "venv\Scripts\activate.bat"
) else (
    echo [!] venv не найден, используется глобальный Python.
)
echo.

%PYTHON% --version
if %ERRORLEVEL% NEQ 0 (
    echo [ОШИБКА] Python не найден.
    pause
    exit /b 1
)
echo.

echo [*] Установка зависимостей...
%PYTHON% -m pip install --upgrade pip
%PYTHON% -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [ОШИБКА] Не удалось установить зависимости.
    pause
    exit /b %ERRORLEVEL%
)
echo.

echo [*] Установка браузеров Playwright (Chromium)...
%PYTHON% -m playwright install chromium
if %ERRORLEVEL% NEQ 0 (
    echo [ПРЕДУПРЕЖДЕНИЕ] Playwright browsers не установились.
)
echo.

echo [*] Очистка старых артефактов...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "VideoManager.spec" del /q "VideoManager.spec"
echo.

echo [*] Запуск PyInstaller (onedir)...
echo.

%PYTHON% -m PyInstaller --noconfirm --clean --onedir --name "VideoManager" ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --add-data "image;image" ^
    --add-data "_dop;_dop" ^
    --add-data "addon\youtube_downloader\templates;addon\youtube_downloader\templates" ^
    --add-data "addon\instagram_downloader\templates;addon\instagram_downloader\templates" ^
    --collect-all playwright ^
    --collect-all cv2 ^
    --collect-all PIL ^
    --collect-all imagehash ^
    --collect-all yt_dlp ^
    --collect-all Cryptodome ^
    --collect-all instagrapi ^
    --hidden-import=flask ^
    --hidden-import=flask.json ^
    --hidden-import=flask.json.provider ^
    --hidden-import=werkzeug.security ^
    --hidden-import=werkzeug.utils ^
    --hidden-import=jinja2 ^
    --hidden-import=sqlite3 ^
    app.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ОШИБКА] Сборка не удалась.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ===================================================
echo СБОРКА УСПЕШНО ЗАВЕРШЕНА!
echo ===================================================
echo EXE: %cd%\dist\VideoManager\VideoManager.exe
echo.
pause
endlocal