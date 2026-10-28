# 纳西妲一键启动脚本
# 1. 检测 GPT-SoVITS 语音服务（9880），没在跑则启动并等待就绪
# 2. 启动纳西妲主程序（当前窗口交互）
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$host.ui.RawUI.WindowTitle = '纳西妲启动器'

$runtime   = 'E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006\runtime\python.exe'
$sovitsDir = 'E:\fariy\GPT-SoVITS\GPT-SoVITS-v2pro-20261006'
$brain     = 'E:\fariy\ai\naxida_ai\naxida_voice_brain.py'

function Test-Port($port) {
    return Test-NetConnection -ComputerName 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue
}

# ---- 1. 语音服务 ----
if (Test-Port 9880) {
    Write-Host '[OK] GPT-SoVITS 语音服务已在运行' -ForegroundColor Green
} else {
    Write-Host '[..] 正在启动 GPT-SoVITS 语音服务（首次加载约 15~30 秒）...' -ForegroundColor Cyan
    Start-Process -FilePath $runtime -ArgumentList 'api_v2.py' -WorkingDirectory $sovitsDir
    $ready = $false
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep 2
        if (Test-Port 9880) { $ready = $true; break }
        Write-Host '    等待模型加载中...'
    }
    if ($ready) {
        Write-Host '[OK] 语音服务已就绪' -ForegroundColor Green
    } else {
        Write-Host '[ERR] 语音服务启动超时，请查看 GPT-SoVITS 窗口的报错' -ForegroundColor Red
        Read-Host '按回车退出'
        exit 1
    }
}

# ---- 2. 主程序 ----
Write-Host '[..] 启动纳西妲...' -ForegroundColor Cyan
& $runtime $brain
