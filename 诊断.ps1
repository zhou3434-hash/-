# =============================================================
#  Travel Assistant - environment diagnostics (PowerShell version)
#
#  Prefer this over 诊断.bat: PowerShell reads UTF-8 correctly, so
#  Chinese output is never garbled.
# =============================================================

Set-Location -LiteralPath $PSScriptRoot
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$env:PYTHONPATH       = Join-Path $PSScriptRoot 'src'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8       = '1'

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$fail   = 0

function Line($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }

Write-Host ''
Line '  ============================================================' Cyan
Line '   旅行出行助手 —— 环境诊断' Cyan
Line '  ============================================================' Cyan
Write-Host ''

# ---- 1 ----
Line '[1/6] Python 虚拟环境'
if (Test-Path -LiteralPath $python) {
    Line "      OK    $python" Green
} else {
    Line "      FAIL  未找到：$python" Red
    Line '            执行：uv venv --python 3.14' Yellow
    Line '                  uv sync --extra dev' Yellow
    $fail = 1
}

# ---- 2 ----
Write-Host ''
Line '[2/6] Python 及依赖'
if (Test-Path -LiteralPath $python) {
    $out = & $python -c "import sys,openai,fastapi,uvicorn,httpx,pydantic,rich;print('Python',sys.version.split()[0])" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Line "      OK    $out" Green
        Line '      OK    openai / fastapi / uvicorn / httpx / pydantic / rich' Green
    } else {
        Line '      FAIL  依赖导入失败，请执行：uv sync --extra dev' Red
        Line "            $out" DarkGray
        $fail = 1
    }
} else {
    Line '      SKIP  无 Python' DarkGray
}

# ---- 3 ----
Write-Host ''
Line '[3/6] .env 中的 API 密钥'
$envFile = Join-Path $PSScriptRoot '.env'
if (Test-Path -LiteralPath $envFile) {
    $code = "import sys;sys.path.insert(0,'src');from travel_assistant.config import get_settings;s=get_settings();k=s.deepseek_api_key;print('KEY',k[:6]+'...'+k[-4:] if k else 'EMPTY');print('MODEL',s.deepseek_model);print('TOKENS',s.deepseek_max_tokens);sys.exit(0 if k else 1)"
    $out = & $python -c $code 2>&1
    if ($LASTEXITCODE -eq 0) {
        Line "      OK    密钥已加载 $($out[0] -replace '^KEY ','')" Green
        Line "      OK    模型 $($out[1] -replace '^MODEL ','')　max_tokens $($out[2] -replace '^TOKENS ','')" Green
    } else {
        Line '      FAIL  .env 存在，但 DEEPSEEK_API_KEY 为空' Red
        $fail = 1
    }
} else {
    Line '      FAIL  未找到 .env' Red
    Line '            执行：copy .env.example .env  然后填入密钥' Yellow
    $fail = 1
}

# ---- 4 ----
Write-Host ''
Line '[4/6] 8000 端口占用情况'
$busy = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Line "      WARN  8000 已被占用（PID $($busy[0].OwningProcess)），已有服务在运行" Yellow
} else {
    Line '      OK    8000 空闲' Green
}

# ---- 5 ----
Write-Host ''
Line '[5/6] 服务启动测试'
if ($fail -eq 0) {
    $logDir = Join-Path $PSScriptRoot 'data'
    if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    $log = Join-Path $logDir 'diag-server.log'

    $proc = Start-Process -FilePath $python -ArgumentList '-m', 'travel_assistant.server' `
        -WorkingDirectory $PSScriptRoot -RedirectStandardOutput $log `
        -RedirectStandardError (Join-Path $logDir 'diag-server.err.log') `
        -PassThru -WindowStyle Hidden

    $ready = $false
    for ($i = 1; $i -le 30; $i++) {
        if ($proc.HasExited) { break }
        try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8000); $c.Close(); $ready = $true; break }
        catch { Start-Sleep -Milliseconds 700 }
    }

    if ($ready) {
        Line '      OK    服务已监听 127.0.0.1:8000' Green
        try {
            $h = Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 15
            Line "      OK    /api/health  model=$($h.model)  景点=$($h.attractions)  工具=$($h.tools.Count)" Green
            Line "      OK    网页界面 HTTP $((Invoke-WebRequest 'http://127.0.0.1:8000/' -UseBasicParsing -TimeoutSec 15).StatusCode)" Green
        } catch {
            Line "      FAIL  /api/health 请求失败：$($_.Exception.Message)" Red
            $fail = 1
        }
    } else {
        Line '      FAIL  服务未能在 30 秒内启动' Red
        foreach ($f in @($log, (Join-Path $logDir 'diag-server.err.log'))) {
            if (Test-Path -LiteralPath $f) { Line ((Get-Content -LiteralPath $f -Tail 12 -Encoding UTF8) -join "`n") DarkGray }
        }
        $fail = 1
    }

    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
} else {
    Line '      SKIP  请先修复上面的错误' DarkGray
}

# ---- 6 ----
Write-Host ''
Line '[6/6] 关键文件存在性'
$need = @(
    'src\travel_assistant\agent.py',
    'src\travel_assistant\server.py',
    'src\travel_assistant\cli.py',
    'src\travel_assistant\tools\attractions_data.py',
    'web\index.html'
)
foreach ($n in $need) {
    $p = Join-Path $PSScriptRoot $n
    if (Test-Path -LiteralPath $p) { Line "      OK    $n" Green } else { Line "      FAIL  缺少 $n" Red; $fail = 1 }
}

# ---- 结论 ----
Write-Host ''
Line '  ============================================================' Cyan
if ($fail -eq 0) {
    Line '   结论：全部检查通过' Green
    Write-Host ''
    Line '   启动方式：' Gray
    Line '     启动网页.ps1      网页版（推荐）' White
    Line '     启动命令行.ps1    命令行版' White
    Line '     停止服务.ps1      停止后台服务' White
    Write-Host ''
    Line '   注意：网页版的服务在后台运行，本窗口关闭不影响。' Gray
} else {
    Line '   结论：发现问题，请看上面的 FAIL 行' Red
}
Line '  ============================================================' Cyan
Write-Host ''
Read-Host '  按回车键退出'
