@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

cd /d "%~dp0"
echo ===================================================
echo  Запуск Video Manager
echo ===================================================
echo.

REM --- 1. Проверка Python -----------------------------------------
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ОШИБКА] Python не найден в PATH.
    echo Установите Python 3.12: https://www.python.org/downloads/
    echo При установке отметьте "Add Python to PATH".
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [*] Найден Python !PYVER!

REM --- 2. venv ----------------------------------------------------
if not exist "venv\Scripts\python.exe" (
    echo [*] Виртуальное окружение не найдено. Создаю...
    python -m venv venv
    if !ERRORLEVEL! NEQ 0 (
        echo [ОШИБКА] Не удалось создать venv.
        pause
        exit /b 1
    )
    echo [*] venv создан.
) else (
    echo [*] Использую существующий venv.
)

set PY=venv\Scripts\python.exe

REM --- 3. Зависимости --------------------------------------------
echo.
echo [*] Проверка зависимостей...
"%PY%" -m pip install --upgrade pip --quiet
"%PY%" -m pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [ОШИБКА] Не удалось установить зависимости.
    pause
    exit /b 1
)

REM --- 4. Playwright Chromium -------------------------------------
echo [*] Проверка браузеров Playwright...
"%PY%" -m playwright install chromium
if %ERRORLEVEL% NEQ 0 (
    echo [ПРЕДУПРЕЖДЕНИЕ] Playwright browsers не установились.
)

REM --- 5. Запуск --------------------------------------------------
echo.
echo ===================================================
echo  Сервер запущен: http://127.0.0.1:5000
echo  Закрыть — Ctrl+C в этом окне.
echo ===================================================
echo.

"%PY%" app.py

pause
endlocal