@echo off
title 纳西妲 Web
echo ========================================
echo   纳西妲 Web 启动中...
echo ========================================

rem --- 检测并启动 TTS 服务 (GPT-SoVITS) ---
echo [1/2] 检查语音合成服务...
netstat -ano | findstr "LISTENING" | findstr ":9880" >nul 2>&1
if %errorlevel% equ 0 (
    echo     TTS 服务已在运行，跳过启动
    goto :tts_ready
)

echo     正在启动 TTS 服务...
start "纳西妲TTS" /D "E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006" "runtime\python.exe" api_v2.py -a 127.0.0.1 -p 9880
set /a _wait=0
:wait_tts
timeout /t 1 /nobreak >nul
netstat -ano | findstr "LISTENING" | findstr ":9880" >nul 2>&1
if %errorlevel% equ 0 goto :tts_ready
set /a _wait+=1
if %_wait% lss 30 goto :wait_tts
echo     [警告] TTS 启动超时，仍将继续启动网页

:tts_ready
echo     TTS 服务已就绪

rem --- 启动 Flask 网页后端 ---
echo [2/2] 正在启动网页后端...
cd /d "E:\fariy\ai\naxida_ai\web"
start "" http://127.0.0.1:5000
"E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006\runtime\python.exe" app.py
pause
