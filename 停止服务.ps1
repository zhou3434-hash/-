# =============================================================
#  Travel Assistant - stop the background web server
# =============================================================

$env:PYTHONPATH = Join-Path $PSScriptRoot 'src'

$found = @()
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.CommandLine -and $_.CommandLine -match 'travel_assistant\.server') {
        $found += $_
    }
}

if (-not $found) {
    Write-Host '  没有找到正在运行的旅行出行助手服务。' -ForegroundColor Yellow
    Read-Host '  按回车键退出'
    exit 0
}

foreach ($p in $found) {
    Write-Host "  停止服务进程 PID $($p.ProcessId)" -ForegroundColor Yellow
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2
$still = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($still) {
    Write-Host '  [警告] 8000 端口仍有监听，可能被其他程序占用。' -ForegroundColor Red
} else {
    Write-Host '  [完成] 服务已停止，8000 端口已释放。' -ForegroundColor Green
}
Write-Host ''
Read-Host '  按回车键退出'
