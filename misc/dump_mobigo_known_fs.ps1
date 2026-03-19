param(
    [string]$OutputRoot = "C:\Users\Max\Desktop\live_fs_dump",
    [int]$ReconnectTimeoutSeconds = 180,
    [int]$PostFailureSleepSeconds = 15
)

$ErrorActionPreference = "Stop"

$cliPath = "C:\Users\Max\Desktop\mobigo_re\mobigo_cli.py"
$manifestPath = "C:\Users\Max\Desktop\mobigo_re\artifacts\capture_reads_smoke\_manifest_reads.json"
$logPath = Join-Path $OutputRoot "_dump_log.txt"

function Test-VTechPresent {
    $disk = Get-CimInstance Win32_DiskDrive | Where-Object {
        $_.PNPDeviceID -match "VID_0F88&PID_2D40" -or $_.Model -like "VTECH USB-MSDC DISK A*"
    } | Select-Object -First 1

    if (-not $disk) {
        return $false
    }

    try {
        return (Test-Path "D:\")
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

function RemotePathToLocalPath {
    param(
        [string]$RemotePath,
        [string]$BasePath
    )

    $normalized = $RemotePath.Replace("/", "\").Trim()
    if ($normalized.StartsWith("A:\", [System.StringComparison]::OrdinalIgnoreCase)) {
        $parts = @("A") + ($normalized.Substring(3).Split("\", [System.StringSplitOptions]::RemoveEmptyEntries))
        $result = $BasePath
        foreach ($part in $parts) {
            $result = Join-Path $result $part
        }
        return $result
    }

    if ($normalized.StartsWith("\")) {
        $parts = @("_root") + ($normalized.TrimStart("\").Split("\", [System.StringSplitOptions]::RemoveEmptyEntries))
        $result = $BasePath
        foreach ($part in $parts) {
            $result = Join-Path $result $part
        }
        return $result
    }

    $safe = $normalized.Replace(":", "").Replace("\", "_")
    return Join-Path $BasePath $safe
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

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$manifestEntries = @()
if (Test-Path $manifestPath) {
    $manifestEntries = Get-Content $manifestPath -Raw | ConvertFrom-Json
}

$priority = @(
    "A:\PHO\00000009.PHO",
    "A:\PHO\00000001.PHO",
    "A:\PHO\00000002.PHO",
    "A:\PHO\00000003.PHO",
    "A:\PHO\00000004.PHO",
    "A:\PHO\00000005.PHO",
    "A:\PHO\00000006.PHO",
    "A:\PHO\00000007.PHO",
    "A:\PHO\00000008.PHO",
    "A:\PHO\00000010.PHO",
    "A:\PHO\00000011.PHO",
    "A:\PHO\00000012.PHO",
    "A:\PHO\00000013.PHO",
    "A:\USENG\EBOOK.MBA",
    "A:\USENG\MM.MBA",
    "A:\USENG\UB.MBA",
    "A:\DEFAULT\MGB_PTCH.BIN",
    "A:\DEFAULT\MM.MBA",
    "A:\DEFAULT\UB.MBA",
    "A:\DEFAULT\CG",
    "A:\DEFAULT\CS",
    "A:\DEFAULT\WT",
    "\ETC\PROFILE.DAT",
    "\USENG\BOKSORT.LST",
    "\USENG\MBASORT.LST"
)

$fromManifest = @($manifestEntries | ForEach-Object { [string]$_.path })
$uniquePaths = @($priority + $fromManifest) | Select-Object -Unique

Write-Log ("starting dump paths={0}" -f $uniquePaths.Count)

$successCount = 0
$skipCount = 0
$completed = New-Object System.Collections.Generic.List[string]
$failed = New-Object System.Collections.Generic.List[string]

foreach ($remotePath in $uniquePaths) {
    $outputPath = RemotePathToLocalPath -RemotePath $remotePath -BasePath $OutputRoot
    $outputDir = Split-Path -Parent $outputPath
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    if (Test-Path $outputPath) {
        Write-Log ("skip existing {0}" -f $remotePath)
        [void]$completed.Add($remotePath)
        continue
    }

    $pathSucceeded = $false
    $pathEndedNonTransient = $false
    $attempt = 0
    while (-not $pathSucceeded) {
        $attempt++
        if (-not (Wait-VTechReady -TimeoutSeconds $ReconnectTimeoutSeconds)) {
            Write-Log ("device not responding before {0} attempt={1}; waiting {2}s" -f $remotePath, $attempt, $PostFailureSleepSeconds)
            Start-Sleep -Seconds $PostFailureSleepSeconds
            continue
        }

        Write-Log ("read {0} attempt={1}" -f $remotePath, $attempt)
        $stdoutFile = [IO.Path]::GetTempFileName()
        $stderrFile = [IO.Path]::GetTempFileName()
        $process = Start-Process -FilePath "python" `
            -ArgumentList @($cliPath, "read-file", $remotePath, $outputPath) `
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

        if ($process.ExitCode -eq 0 -and (Test-Path $outputPath)) {
            $size = (Get-Item $outputPath).Length
            Write-Log ("ok {0} bytes={1} attempt={2}" -f $remotePath, $size, $attempt)
            $successCount++
            $pathSucceeded = $true
            [void]$completed.Add($remotePath)
            break
        }

        if (Test-Path $outputPath) {
            Remove-Item -Force $outputPath
        }

        $joined = ($output | Out-String).Trim()
        if ([string]::IsNullOrWhiteSpace($joined)) {
            $joined = "unknown failure"
        }
        Write-Log ("fail {0} attempt={1} error={2}" -f $remotePath, $attempt, $joined)

        if (-not (Test-TransientFailure -ErrorText $joined)) {
            Write-Log ("non-transient failure {0} attempt={1}" -f $remotePath, $attempt)
            $pathEndedNonTransient = $true
            break
        }

        Write-Log ("device not responding for {0} attempt={1}; waiting {2}s" -f $remotePath, $attempt, $PostFailureSleepSeconds)
        Start-Sleep -Seconds $PostFailureSleepSeconds
    }

    if (-not $pathSucceeded) {
        $skipCount++
        [void]$failed.Add($remotePath)
        continue
    }
}

Write-Log ("finished ok={0} skipped={1}" -f $successCount, $skipCount)
if ($failed.Count -gt 0) {
    Write-Log ("failed-paths={0}" -f ($failed -join ";"))
}
