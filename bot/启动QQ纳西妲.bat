@echo off
chcp 65001 >nul
title Naxida QQ Bot
cd /d "%~dp0"

set "NAPCAT_DIR=E:\fariy\chatqq\NapCat.Shell.Windows.OneKey\NapCat.52230.Shell"
set "BOT_QQ=3967286818"

echo ========================================
echo   Naxida QQ Bot - one click start
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found in PATH
    echo         Please install Python and add it to PATH
    echo.
    pause
    exit /b
)

netstat -ano | findstr ":3001" | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] NapCat already running on port 3001
    goto start_bot
)

echo [..] starting NapCat with quick login (%BOT_QQ%)...
if not exist "%NAPCAT_DIR%\NapCatWinBootMain.exe" (
    echo [ERROR] NapCat not found: %NAPCAT_DIR%
    echo.
    pause
    exit /b
)
start "NapCat" /min /d "%NAPCAT_DIR%" NapCatWinBootMain.exe %BOT_QQ%

echo [..] waiting for port 3001 (max 40s)...
set /a waited=0
:wait_port
ping -n 3 127.0.0.1 >nul
netstat -ano | findstr ":3001" | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 goto port_ready
set /a waited+=2
if %waited% geq 40 (
    echo [ERROR] NapCat is still not listening after 40s.
    echo         Open http://127.0.0.1:6099/webui/ and scan the QR code, then run this file again.
    echo.
    pause
    exit /b
)
goto wait_port

:port_ready
echo [OK] NapCat ready on port 3001
echo [TIP] If QQ login has expired, open http://127.0.0.1:6099/webui/ once to scan the QR code.
echo.

:start_bot
python -u run_bot.py

echo.
echo [EXIT] bot stopped
pause >nul
