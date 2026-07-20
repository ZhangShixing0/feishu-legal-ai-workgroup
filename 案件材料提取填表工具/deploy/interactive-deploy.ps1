param(
    [Parameter(Mandatory = $true)]
    [string]$Server,
    [string]$PublicBaseUrl = "https://legal.example.com",
    [string]$PackagePath = (Join-Path $PSScriptRoot "..\..\outputs\material-extractor-server.tar.gz")
)

$ErrorActionPreference = "Stop"

$package = (Resolve-Path $PackagePath).Path
$remotePackage = "/tmp/material-extractor-server-1.2.0.tar.gz"
$statusPath = Join-Path $PSScriptRoot "interactive-deploy-status.json"
$sshOptions = @(
    "-4",
    "-o", "IPQoS=none",
    "-o", "ConnectTimeout=15",
    "-o", "ConnectionAttempts=2",
    "-o", "KexAlgorithms=curve25519-sha256",
    "-o", "Ciphers=aes128-ctr",
    "-o", "MACs=hmac-sha2-256",
    "-o", "HostKeyAlgorithms=ssh-ed25519",
    "-o", "Compression=no",
    "-o", "StrictHostKeyChecking=accept-new"
)

function Save-Status([bool]$Ok, [string]$Message) {
    $payload = @{
        ok = $Ok
        message = $Message
        time = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    } | ConvertTo-Json
    [IO.File]::WriteAllText($statusPath, $payload, [Text.UTF8Encoding]::new($false))
}

try {
    if (Test-Path $statusPath) { Remove-Item -LiteralPath $statusPath -Force }
    Clear-Host
    Write-Host "Case Material Extractor - Server Deployment" -ForegroundColor Cyan
    Write-Host "Server: $Server"
    Write-Host "Target: $($PublicBaseUrl.TrimEnd('/'))/extractor/"
    Write-Host ""
    Write-Host "The root password is requested twice: upload, then install." -ForegroundColor Yellow
    Write-Host "SSH does not display password characters while you type."
    Write-Host ""

    Write-Host "[1/2] Uploading package..." -ForegroundColor Cyan
    & scp.exe @sshOptions $package "${Server}:$remotePackage"
    if ($LASTEXITCODE -ne 0) { throw "Package upload failed, exit code $LASTEXITCODE" }

    Write-Host ""
    Write-Host "[2/2] Installing isolated service and Nginx route..." -ForegroundColor Cyan
    $remoteCommand = "mkdir -p /tmp/material-extractor-installer && tar -xzf $remotePackage -C /tmp/material-extractor-installer deploy/install-server.sh && bash /tmp/material-extractor-installer/deploy/install-server.sh $remotePackage"
    & ssh.exe @sshOptions $Server $remoteCommand
    if ($LASTEXITCODE -ne 0) { throw "Server installation failed, exit code $LASTEXITCODE" }

    Save-Status $true "Server deployment completed"
    Write-Host ""
    Write-Host "Deployment completed: $($PublicBaseUrl.TrimEnd('/'))/extractor/" -ForegroundColor Green
} catch {
    Save-Status $false $_.Exception.Message
    Write-Host ""
    Write-Host "Deployment did not complete: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    Write-Host ""
    Read-Host "Press Enter to close"
}
