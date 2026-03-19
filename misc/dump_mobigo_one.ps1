param(
    [string]$RemotePath = "A:\USENG\00000071.MBA",
    [string]$OutputPath = "C:\Users\Max\Desktop\00000071.MBA",
    [int]$ReconnectTimeoutSeconds = 180,
    [int]$PostFailureSleepSeconds = 15
)

$ErrorActionPreference = "Stop"

$cliPath = "C:\Users\Max\Desktop\mobigo_re\mobigo_cli.py"
$logPath = [IO.Path]::ChangeExtension($OutputPath, ".log.txt")

function Test-VTechPresent {
    $disk = Get-CimInstance Win32_DiskDrive | Where-Object {
        $_.PNPDeviceID -match "VID_0F88&PID_2D40" -or $_.Model -like "VTECH USB-MSDC DISK A*"
    } | Select-Object -First 1

    if (-not $disk) {
        return $false
    }

    try {
        return (Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Name -eq "D" }).Count -gt 0
    }
    catch {
        return $false
    }
}

function Wait-VTechReady {
    param(
        [int]$TimeoutSeconds = 180
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-VTechPresent) {
            return $true
        }

        Start-Sleep -Seconds 2
    }

    return $false
}

function Write-Log {
    param(
        [string]$Message
    )

    $line = "{0:u} {1}" -f (Get-Date), $Message
    $line | Tee-Object -FilePath $logPath -Append
}

function Test-TransientFailure {
    param(
        [string]$ErrorText
    )

    $patterns = @(
        "device is not ready",
        "device which does not exist",
        "DLL_LSInitUSBDevices failed",
        "Could not auto-detect the MobiGo device",
        "wrong diskette",
        "ReadFile",
        "GetMsUsbDeviceVolume",
        "failed to open"
    )

    foreach ($pattern in $patterns) {
        if ($ErrorText.IndexOf($pattern, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
    }

    return $false
}

$outputDir = Split-Path -Parent $OutputPath
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
}

$attempt = 0
while ($true) {
    $attempt++
    if (-not (Wait-VTechReady -TimeoutSeconds $ReconnectTimeoutSeconds)) {
        Write-Log ("device not responding before {0} attempt={1}; waiting {2}s" -f $RemotePath, $attempt, $PostFailureSleepSeconds)
        Start-Sleep -Seconds $PostFailureSleepSeconds
        continue
    }

    Write-Log ("read {0} attempt={1}" -f $RemotePath, $attempt)
    $stdoutFile = [IO.Path]::GetTempFileName()
    $stderrFile = [IO.Path]::GetTempFileName()
    $process = Start-Process -FilePath "python" `
        -ArgumentList @($cliPath, "read-file", $RemotePath, $OutputPath) `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $stdoutFile `
        -RedirectStandardError $stderrFile
    $output = @(
        if (Test-Path $stdoutFile) { Get-Content $stdoutFile -Raw }
        if (Test-Path $stderrFile) { Get-Content $stderrFile -Raw }
    ) -join [Environment]::NewLine
    Remove-Item -Force $stdoutFile, $stderrFile

    if ($process.ExitCode -eq 0 -and (Test-Path $OutputPath)) {
        $size = (Get-Item $OutputPath).Length
        Write-Log ("ok {0} bytes={1} attempt={2}" -f $RemotePath, $size, $attempt)
        break
    }

    if (Test-Path $OutputPath) {
        Remove-Item -Force $OutputPath
    }

    $joined = ($output | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($joined)) {
        $joined = "unknown failure"
    }
    Write-Log ("fail {0} attempt={1} error={2}" -f $RemotePath, $attempt, $joined)

    if (-not (Test-TransientFailure -ErrorText $joined)) {
        Write-Log ("non-transient failure {0} attempt={1}" -f $RemotePath, $attempt)
        break
    }

    Write-Log ("device not responding for {0} attempt={1}; waiting {2}s" -f $RemotePath, $attempt, $PostFailureSleepSeconds)
    Start-Sleep -Seconds $PostFailureSleepSeconds
}
