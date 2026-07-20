$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "未找到 Python。请先运行 setup.bat。"
    }
    $Python = $command.Source
}

if (-not (Test-Path -LiteralPath (Join-Path $Root ".env.feishu"))) {
    throw "缺少 .env.feishu。请先运行 setup.bat 并填写飞书应用凭证。"
}

& $Python -c "import requests, lark_oapi"
if ($LASTEXITCODE -ne 0) {
    throw "依赖未安装完整。请重新运行 setup.bat。"
}

$env:CASE_EXTRACTOR_NO_BROWSER = "1"
$env:CASE_EXTRACTOR_STRICT_PORT = "1"
$children = @()

function Wait-Health([string]$Url, [string]$Name) {
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                Write-Host "$Name 已就绪：$Url" -ForegroundColor Green
                return
            }
        }
        catch {
            Start-Sleep -Milliseconds 300
        }
    }
    throw "$Name 启动超时：$Url"
}

try {
    $children += Start-Process -WindowStyle Hidden -PassThru -FilePath $Python `
        -ArgumentList @("server.py", "--no-browser") -WorkingDirectory $Root
    $children += Start-Process -WindowStyle Hidden -PassThru -FilePath $Python `
        -ArgumentList @("案件材料提取填表工具\server.py") -WorkingDirectory $Root

    Wait-Health "http://127.0.0.1:8765/api/schema" "诉状生成服务"
    Wait-Health "http://127.0.0.1:8777/api/health" "材料提取服务"

    Write-Host "飞书工作群长连接运行中；按 Ctrl+C 停止。" -ForegroundColor Cyan
    & $Python -m feishu_workgroup.run
    exit $LASTEXITCODE
}
finally {
    foreach ($child in $children) {
        if ($null -ne $child -and -not $child.HasExited) {
            Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
