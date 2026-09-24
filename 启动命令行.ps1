# =============================================================
#  Travel Assistant - Command Line Interface (PowerShell launcher)
# =============================================================

Set-Location -LiteralPath $PSScriptRoot

$env:PYTHONPATH       = Join-Path $PSScriptRoot 'src'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8       = '1'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host ''
    Write-Host '  [错误] 找不到虚拟环境 .venv' -ForegroundColor Red
    Write-Host '         请先执行：' -ForegroundColor Yellow
    Write-Host '             uv venv --python 3.14' -ForegroundColor Yellow
    Write-Host '             uv sync --extra dev' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  按回车键退出'
    exit 1
}

& $python -m travel_assistant.cli @args

if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host "  [提示] 程序退出码：$LASTEXITCODE" -ForegroundColor Yellow
    Read-Host '  按回车键退出'
}
