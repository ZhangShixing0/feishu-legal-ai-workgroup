$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root

function Resolve-Python {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    foreach ($name in @("python.exe", "python")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            return $command.Source
        }
    }
    throw "未找到 Python 3.10 或更高版本。"
}

$BootstrapPython = Resolve-Python
$version = & $BootstrapPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "无法运行 Python：$BootstrapPython"
}
$parts = $version.Trim().Split(".")
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 10)) {
    throw "需要 Python 3.10+，当前版本为 $version。"
}

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "[1/3] 创建虚拟环境 .venv ..." -ForegroundColor Cyan
    & $BootstrapPython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败。" }
}

Write-Host "[2/3] 安装 Python 依赖 ..." -ForegroundColor Cyan
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "升级 pip 失败。" }
& $VenvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "安装项目依赖失败。" }

$EnvFile = Join-Path $Root ".env.feishu"
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item -LiteralPath (Join-Path $Root ".env.feishu.example") -Destination $EnvFile
    Write-Host "已创建 .env.feishu，请在启用飞书工作群前填写应用凭证。" -ForegroundColor Yellow
}

Write-Host "[3/3] 安装完成。" -ForegroundColor Green
Write-Host "- 仅启动诉状生成台：run.bat"
Write-Host "- 启动完整飞书工作群：run_feishu_v1.bat"
Write-Host "- 运行全部检查：.venv\Scripts\python.exe tools\run_checks.py"
