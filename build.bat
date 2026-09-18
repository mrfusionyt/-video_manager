@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ===================================================
echo Сборка Video Manager в EXE (режим onedir)
echo ===================================================
echo.

REM 1. Переход в директорию, где находится сам .bat-файл
cd /d "%~dp0"
echo [*] Текущая директория: %cd%
echo.

REM 2. Определение Python: если есть venv — используем его, иначе системный
set PYTHON=python
if exist "venv\Scripts\python.exe" (
    echo [*] Найдено виртуальное окружение venv, использую его.
    set PYTHON=venv\Scripts\python.exe
    REM Активация не обязательна, но полезна для дочерних процессов
    call "venv\Scripts\activate.bat"
) else (
    echo [!] Виртуальное окружение venv не найдено. Используется глобальный Python.
)
echo.

REM 3. Проверка, что Python работает
%PYTHON% --version
if %ERRORLEVEL% NEQ 0 (
    echo [ОШИБКА] Python не найден. Установите Python 3.10+ и добавьте его в PATH.
    pause
    exit /b 1
)
echo.

REM 4. Установка/обновление зависимостей
echo [*] Проверка и установка зависимостей из requirements.txt...
%PYTHON% -m pip install --upgrade pip
%PYTHON% -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [ОШИБКА] Не удалось установить зависимости. Проверьте requirements.txt.
    pause
    exit /b %ERRORLEVEL%
)
echo.

REM 5. Установка браузеров Playwright (Chromium) через python -m
echo [*] Установка браузеров Playwright (Chromium)...
REM Если нужен переносимый EXE — раскомментируйте следующую строку:
REM set PLAYWRIGHT_BROWSERS_PATH=0
%PYTHON% -m playwright install chromium
if %ERRORLEVEL% NEQ 0 (
    echo [ПРЕДУПРЕЖДЕНИЕ] Не удалось установить браузеры Playwright.
    echo Убедитесь, что Playwright корректно установлен в окружении.
)
echo.

REM 6. Очистка предыдущих сборок
echo [*] Очистка старых артефактов сборки...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "VideoManager.spec" del /q "VideoManager.spec"
echo.

REM 7. Запуск PyInstaller через python -m (не зависит от PATH)
echo [*] Запуск сборки через PyInstaller (onedir)...
echo.

%PYTHON% -m PyInstaller --noconfirm --clean --onedir --name "VideoManager" ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --add-data "_dop;_dop" ^
    --collect-all playwright ^
    --collect-all cv2 ^
    --collect-all PIL ^
    --collect-all imagehash ^
    --collect-all yt_dlp ^
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
    echo [ОШИБКА] Сборка не удалась. Просмотрите сообщения выше.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ===================================================
echo СБОРКА УСПЕШНО ЗАВЕРШЕНА!
echo ===================================================
echo.
echo Исполняемый файл находится здесь:
echo %cd%\dist\VideoManager\VideoManager.exe
echo.
echo Если приложение ищет папку _dop рядом с .exe, скопируйте её вручную
echo в dist\VideoManager\_dop (или используйте resource_path в app.py).
echo.
pause
endlocal