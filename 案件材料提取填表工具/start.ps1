$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot
$logPath = Join-Path $PSScriptRoot "startup.log"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$timestamp] Launcher started in $PSScriptRoot" | Set-Content -LiteralPath $logPath -Encoding UTF8

function Add-Log([string]$Message) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Write-Host $line
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

try {
    $candidates = @()

    $pythonCommands = @(Get-Command python.exe -All -ErrorAction SilentlyContinue)
    foreach ($pythonCommand in $pythonCommands) {
        $candidates += [pscustomobject]@{
            Executable = $pythonCommand.Source
            PrefixArgs = @()
            Label = $pythonCommand.Source
        }
    }

    # The Windows py launcher can exist even when it has no registered Python.
    # Try concrete python.exe commands first and isolate probe errors below.
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        $candidates += [pscustomobject]@{
            Executable = $pyLauncher.Source
            PrefixArgs = @("-3")
            Label = "Python launcher"
        }
    }

    $selected = $null
    foreach ($candidate in $candidates) {
        $probeArgs = @($candidate.PrefixArgs) + @(
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}|{sys.executable}')"
        )
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $probeOutput = @(& $candidate.Executable @probeArgs 2>&1)
            $probeExitCode = $LASTEXITCODE
        }
        catch {
            $probeOutput = @($_.Exception.Message)
            $probeExitCode = 1
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
        $probeLine = ($probeOutput | Select-Object -Last 1).ToString().Trim()

        if ($probeExitCode -eq 0 -and $probeLine -match '^(\d+)\.(\d+)\|(.+)$') {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
                $selected = $candidate
                Add-Log "Using Python $major.$minor at $($Matches[3])"
                break
            }
        }
    }

    if ($null -eq $selected) {
        throw "Python 3.10 or newer was not found. Install Python and enable Add Python to PATH."
    }

    Add-Log "Starting the local tool. The browser should open automatically."
    $serverArgs = @($selected.PrefixArgs) + @("server.py")
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $selected.Executable @serverArgs 2>&1 | Tee-Object -FilePath $logPath -Append
        $serverExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($serverExitCode -ne 0) {
        throw "The local server exited with code $serverExitCode."
    }
    exit 0
}
catch {
    Add-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
