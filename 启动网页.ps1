# =============================================================
#  Travel Assistant - Web Interface (PowerShell launcher)
#
#  Why PowerShell instead of .bat:
#  the system code page is 936 (GBK). .bat files are fragile there
#  (cmd re-parses them per byte, editors mis-guess the encoding,
#  and labels/goto inside if-blocks misbehave). PowerShell handles
#  text as UTF-8 properly, so console output is never garbled.
# =============================================================

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$env:PYTHONPATH      = Join-Path $PSScriptRoot 'src'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8      = '1'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$url    = 'http://127.0.0.1:8000'

Write-Host ''
Write-Host '  旅行出行助手 —— 网页版' -ForegroundColor Cyan
Write-Host '  ==========================================' -ForegroundColor DarkGray
Write-Host ''

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host '  [错误] 找不到虚拟环境 .venv' -ForegroundColor Red
    Write-Host '         请先执行：' -ForegroundColor Yellow
    Write-Host '             uv venv --python 3.14' -ForegroundColor Yellow
    Write-Host '             uv sync --extra dev' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  按回车键退出'
    exit 1
}

# 端口被占用时给出提示，避免误以为新服务已启动
$busy = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host '  [提示] 8000 端口已有服务在运行。' -ForegroundColor Yellow
    Write-Host "         直接打开：$url" -ForegroundColor Yellow
    Write-Host '         若要重启，请先运行 停止服务.ps1' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  按回车键退出'
    exit 0
}

Write-Host '  正在启动服务（日志写入 data\server.log）…' -ForegroundColor Gray

$logDir = Join-Path $PSScriptRoot 'data'
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir 'server.log'

$proc = Start-Process -FilePath $python `
    -ArgumentList '-m', 'travel_assistant.server' `
    -WorkingDirectory $PSScriptRoot `
    -RedirectStandardOutput $log `
    -RedirectStandardError (Join-Path $logDir 'server.err.log') `
    -PassThru -WindowStyle Hidden

# 轮询端口，真正就绪后再开浏览器
$ready = $false
for ($i = 1; $i -le 40; $i++) {
    if ($proc.HasExited) { break }
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', 8000); $c.Close()
        $ready = $true; break
    } catch { Start-Sleep -Milliseconds 700 }
}

if (-not $ready) {
    Write-Host ''
    Write-Host '  [错误] 服务未能在 30 秒内就绪。' -ForegroundColor Red
    Write-Host '         日志末尾：' -ForegroundColor Yellow
    $tail = ''
    foreach ($f in @($log, (Join-Path $logDir 'server.err.log'))) {
        if (Test-Path -LiteralPath $f) { $tail += (Get-Content -LiteralPath $f -Tail 15 -Encoding UTF8) -join "`n" }
    }
    if ($tail) { Write-Host $tail -ForegroundColor DarkGray }
    if (-not $proc.HasExited) { $proc.Kill() }
    Write-Host ''
    Read-Host '  按回车键退出'
    exit 1
}

Write-Host '  [成功] 服务已就绪。' -ForegroundColor Green
Start-Process $url
Write-Host ''
Write-Host "  网页地址：$url" -ForegroundColor Cyan
Write-Host "  服务进程 PID：$($proc.Id)" -ForegroundColor Gray
Write-Host ''
Write-Host '  服务在后台运行，本窗口可以关闭。' -ForegroundColor Gray
Write-Host '  停止服务请运行：停止服务.ps1' -ForegroundColor Gray
Write-Host ''
Write-Host '  按回车键关闭本窗口（服务继续运行）…' -ForegroundColor DarkGray
Read-Host | Out-Null
