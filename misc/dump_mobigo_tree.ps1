param(
    [string]$TreeJsonPath = "C:\Users\Max\Desktop\live_A_tree.json",
    [string]$OutputRoot = "",
    [int]$ReconnectTimeoutSeconds = 180,
    [int]$PostFailureSleepSeconds = 15
)

$ErrorActionPreference = "Stop"

$cliPath = "C:\Users\Max\Desktop\mobigo_re\mobigo_cli.py"

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputRoot = "C:\Users\Max\Desktop\live_tree_dump_$stamp"
}

$logPath = Join-Path $OutputRoot "_dump_log.txt"
$manifestOutPath = Join-Path $OutputRoot "_dump_manifest.json"

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

function Get-TreeFileEntries {
    param(
        [string]$JsonPath
    )

    $tree = Get-Content $JsonPath -Raw | ConvertFrom-Json
    $files = New-Object System.Collections.Generic.List[object]

    foreach ($dir in $tree.PSObject.Properties) {
        foreach ($entry in $dir.Value.entries) {
            if ($entry.kind -ne "file") {
                continue
            }

            $files.Add([pscustomobject]@{
                path = [string]$entry.path
                size = [int64]$entry.size
            })
        }
    }

    return @($files | Sort-Object path -Unique)
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$entries = Get-TreeFileEntries -JsonPath $TreeJsonPath
$entries | ConvertTo-Json -Depth 3 | Set-Content -Path $manifestOutPath -Encoding ascii

Write-Log ("starting tree dump files={0}" -f $entries.Count)

$successCount = 0
$skipCount = 0
$failed = New-Object System.Collections.Generic.List[string]

foreach ($entry in $entries) {
    $remotePath = [string]$entry.path
    $expectedSize = [int64]$entry.size
    $outputPath = RemotePathToLocalPath -RemotePath $remotePath -BasePath $OutputRoot
    $outputDir = Split-Path -Parent $outputPath
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    if (Test-Path $outputPath) {
        $existingSize = (Get-Item $outputPath).Length
        if ($existingSize -eq $expectedSize) {
            Write-Log ("skip existing {0} bytes={1}" -f $remotePath, $existingSize)
            continue
        }

        Remove-Item -Force $outputPath
    }

    $attempt = 0
    $pathSucceeded = $false

    while (-not $pathSucceeded) {
        $attempt++
        if (-not (Wait-VTechReady -TimeoutSeconds $ReconnectTimeoutSeconds)) {
            Write-Log ("device not responding before {0} attempt={1}; waiting {2}s" -f $remotePath, $attempt, $PostFailureSleepSeconds)
            Start-Sleep -Seconds $PostFailureSleepSeconds
            continue
        }

        Write-Log ("read {0} expected={1} attempt={2}" -f $remotePath, $expectedSize, $attempt)
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
            $actualSize = (Get-Item $outputPath).Length
            if ($actualSize -eq $expectedSize) {
                Write-Log ("ok {0} bytes={1} attempt={2}" -f $remotePath, $actualSize, $attempt)
                $successCount++
                $pathSucceeded = $true
                break
            }

            Write-Log ("size-mismatch {0} expected={1} actual={2} attempt={3}" -f $remotePath, $expectedSize, $actualSize, $attempt)
            Remove-Item -Force $outputPath
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
            $skipCount++
            [void]$failed.Add($remotePath)
            break
        }

        Write-Log ("device not responding for {0} attempt={1}; waiting {2}s" -f $remotePath, $attempt, $PostFailureSleepSeconds)
        Start-Sleep -Seconds $PostFailureSleepSeconds
    }
}

Write-Log ("finished ok={0} skipped={1}" -f $successCount, $skipCount)
if ($failed.Count -gt 0) {
    Write-Log ("failed-paths={0}" -f ($failed -join ";"))
}
