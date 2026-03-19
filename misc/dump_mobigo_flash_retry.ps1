param(
    [string]$OutputPath = "",
    [uint32]$FlashSize = 0x04000000,
    [uint32]$ChunkSize = 0x00010000,
    [int]$RetryDelaySeconds = 15
)

$ErrorActionPreference = "Stop"

$cliPath = "C:\Users\Max\Desktop\mobigo_re\mobigo_cli.py"

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = "C:\Users\Max\Desktop\mobigo_flash_dump_$stamp.bin"
}

$logPath = [IO.Path]::ChangeExtension($OutputPath, ".log.txt")
$tempChunk = [IO.Path]::ChangeExtension($OutputPath, ".chunk.tmp")

function Write-Log {
    param(
        [string]$Message
    )

    $line = "{0:u} {1}" -f (Get-Date), $Message
    $line | Tee-Object -FilePath $logPath -Append
}

function Invoke-FlashRead {
    param(
        [uint32]$Offset,
        [uint32]$Length,
        [string]$ChunkPath
    )

    $stdoutFile = [IO.Path]::GetTempFileName()
    $stderrFile = [IO.Path]::GetTempFileName()
    try {
        $process = Start-Process -FilePath "python" `
            -ArgumentList @($cliPath, "read-flash", ("0x{0:X8}" -f $Offset), ("0x{0:X8}" -f $Length), $ChunkPath) `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $stdoutFile `
            -RedirectStandardError $stderrFile
        $output = @(
            if (Test-Path $stdoutFile) { Get-Content $stdoutFile -Raw }
            if (Test-Path $stderrFile) { Get-Content $stderrFile -Raw }
        ) -join [Environment]::NewLine
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Output = ($output | Out-String).Trim()
        }
    }
    finally {
        if (Test-Path $stdoutFile) { Remove-Item -Force $stdoutFile }
        if (Test-Path $stderrFile) { Remove-Item -Force $stderrFile }
    }
}

if ($ChunkSize -eq 0) {
    throw "ChunkSize must be non-zero."
}

$outputDir = Split-Path -Parent $OutputPath
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
}

$resumeOffset = [uint32]0
if (Test-Path $OutputPath) {
    $existingLength = [uint64](Get-Item $OutputPath).Length
    $alignedLength = [uint64]([Math]::Floor($existingLength / $ChunkSize) * $ChunkSize)
    if ($alignedLength -ne $existingLength) {
        Write-Log ("truncating partial output from {0} to aligned {1}" -f $existingLength, $alignedLength)
        $stream = [System.IO.File]::Open($OutputPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try {
            $stream.SetLength([int64]$alignedLength)
        }
        finally {
            $stream.Dispose()
        }
    }
    $resumeOffset = [uint32]$alignedLength
}

Write-Log ("starting flash dump output={0} flash_size=0x{1:X8} chunk_size=0x{2:X8} resume=0x{3:X8}" -f $OutputPath, $FlashSize, $ChunkSize, $resumeOffset)

$outStream = [System.IO.File]::Open($OutputPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    $outStream.Seek([int64]$resumeOffset, [System.IO.SeekOrigin]::Begin) | Out-Null

    $offset = $resumeOffset
    while ($offset -lt $FlashSize) {
        $todo = [uint32][Math]::Min([uint64]$ChunkSize, [uint64]$FlashSize - [uint64]$offset)
        if (Test-Path $tempChunk) {
            Remove-Item -Force $tempChunk
        }

        Write-Log ("read offset=0x{0:X8} size=0x{1:X8} attempt=1" -f $offset, $todo)
        $result = Invoke-FlashRead -Offset $offset -Length $todo -ChunkPath $tempChunk
        $ok = $result.ExitCode -eq 0 -and (Test-Path $tempChunk) -and ((Get-Item $tempChunk).Length -eq $todo)

        if (-not $ok) {
            if (Test-Path $tempChunk) {
                Remove-Item -Force $tempChunk
            }
            $reason = if ([string]::IsNullOrWhiteSpace($result.Output)) { "unknown failure" } else { $result.Output }
            Write-Log ("device disconnected or read failed at offset=0x{0:X8}: {1}" -f $offset, $reason)
            Write-Log ("waiting {0}s before retry" -f $RetryDelaySeconds)
            Start-Sleep -Seconds $RetryDelaySeconds

            Write-Log ("read offset=0x{0:X8} size=0x{1:X8} attempt=2" -f $offset, $todo)
            $result = Invoke-FlashRead -Offset $offset -Length $todo -ChunkPath $tempChunk
            $ok = $result.ExitCode -eq 0 -and (Test-Path $tempChunk) -and ((Get-Item $tempChunk).Length -eq $todo)

            if (-not $ok) {
                if (Test-Path $tempChunk) {
                    Remove-Item -Force $tempChunk
                }
                $reason = if ([string]::IsNullOrWhiteSpace($result.Output)) { "unknown failure" } else { $result.Output }
                Write-Log ("retry failed at offset=0x{0:X8}: {1}" -f $offset, $reason)
                throw "flash dump stopped at offset 0x{0:X8}" -f $offset
            }
        }

        $bytes = [System.IO.File]::ReadAllBytes($tempChunk)
        $outStream.Write($bytes, 0, $bytes.Length)
        $outStream.Flush()
        Remove-Item -Force $tempChunk

        $offset += $todo
        Write-Log ("progress offset=0x{0:X8}/0x{1:X8}" -f $offset, $FlashSize)
    }
}
finally {
    $outStream.Dispose()
    if (Test-Path $tempChunk) {
        Remove-Item -Force $tempChunk
    }
}

Write-Log ("finished output={0}" -f $OutputPath)
