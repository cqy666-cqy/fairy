# QQ 纳西妲一键启动脚本
# 1. 检测 NapCat 是否在线（3001 端口），不在则用快速登录模式启动（session 有效时免扫码）
# 2. 快速登录失败（session 失效/被挤下线）时自动打开扫码页，等待用户扫码
# 3. 启动 QQ 机器人（前台窗口，方便看日志）
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$host.ui.RawUI.WindowTitle = 'QQ纳西妲启动器'

$napcatDir = 'E:\fariy\chatqq\NapCat.Shell.Windows.OneKey\NapCat.52230.Shell'
$napcatExe = Join-Path $napcatDir 'NapCatWinBootMain.exe'
$botQQ     = '3967286818'   # 机器人小号，快速登录参数
$python    = 'D:\python\anaconda\python.exe'
$botScript = 'E:\fariy\ai\naxida_ai\bot\run_bot.py'

function Test-Port($port) {
    return Test-NetConnection -ComputerName 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue
}

# ---- 0. bot 已在运行则直接退出（单实例锁端口 39271）----
if (Test-Port 39271) {
    Write-Host '[OK] QQ纳西妲已经在运行了，无需重复启动' -ForegroundColor Green
    return
}

# ---- 1. NapCat ----
if (Test-Port 3001) {
    Write-Host '[OK] NapCat 已在线（3001 端口正常）' -ForegroundColor Green
} else {
    Write-Host '[..] NapCat 未运行，使用快速登录启动（免扫码）...' -ForegroundColor Cyan
    Start-Process -FilePath $napcatExe -ArgumentList $botQQ -WorkingDirectory $napcatDir

    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep 2
        if (Test-Port 3001) { $ready = $true; break }
    }

    if ($ready) {
        Write-Host '[OK] NapCat 快速登录成功' -ForegroundColor Green
    } else {
        # 快速登录失败：session 失效或被挤下线，需要扫码
        Write-Host '[!!] 快速登录未生效，需要扫码登录' -ForegroundColor Yellow
        Write-Host '     正在打开 NapCat 扫码页面，请用手机 QQ 扫码...' -ForegroundColor Yellow
        Start-Process 'http://127.0.0.1:6099/webui'
        for ($i = 0; $i -lt 90; $i++) {
            Start-Sleep 2
            if (Test-Port 3001) { $ready = $true; break }
        }
        if (-not $ready) {
            Write-Host '[ERR] 等待扫码超时，请重新运行本脚本' -ForegroundColor Red
            Read-Host '按回车退出'
            exit 1
        }
        Write-Host '[OK] 扫码成功，NapCat 已上线' -ForegroundColor Green
    }
}

# ---- 2. 启动 bot（前台窗口，Ctrl+C 或关窗即停止）----
Write-Host '[..] 启动 QQ 纳西妲...' -ForegroundColor Cyan
& $python -u $botScript
