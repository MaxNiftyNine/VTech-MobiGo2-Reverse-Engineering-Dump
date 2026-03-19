$src = 'C:\Users\Max\Downloads\a.MBA'
$dst = 'C:\Users\Max\Downloads\a_lcdinv_poc.MBA'

$hookOffset = 0xA700
$hookExpected = [byte[]](0x02, 0x9E, 0x8C, 0xFE)
$hookPatch = [byte[]](0x90, 0xFE, 0x80, 0x9F) # goto 0x109F80 (byte offset 0x213F00)

$payloadOffset = 0x213F00
$payload = [byte[]](
    0x88, 0xD2,             # push R1 to [SP]
    0x06, 0x93,             # R1 = SR
    0x88, 0xD2,             # push R1 to [SP]
    0x59, 0xF2, 0x00, 0x20, # setb [0x2000], 9
    0x88, 0x90,             # pop R1 from [SP]
    0x01, 0x9D,             # SR = R1
    0x88, 0x90,             # pop R1 from [SP]
    0x02, 0x9E,             # ja +2
    0x8C, 0xFE, 0xE3, 0xD4, # goto 0x0CD4E3
    0x80, 0xFE, 0x83, 0x53  # goto 0x005383 (byte offset 0xA706)
)

$bytes = [System.IO.File]::ReadAllBytes($src)

for ($i = 0; $i -lt $hookExpected.Length; $i++) {
    if ($bytes[$hookOffset + $i] -ne $hookExpected[$i]) {
        throw ("Hook precondition failed at 0x{0:X}: found {1:X2}, expected {2:X2}" -f ($hookOffset + $i), $bytes[$hookOffset + $i], $hookExpected[$i])
    }
}

[System.IO.File]::WriteAllBytes($dst, $bytes)
$out = [System.IO.File]::ReadAllBytes($dst)

[Array]::Copy($hookPatch, 0, $out, $hookOffset, $hookPatch.Length)
[Array]::Copy($payload, 0, $out, $payloadOffset, $payload.Length)

[System.IO.File]::WriteAllBytes($dst, $out)

$sha256 = (Get-FileHash $dst -Algorithm SHA256).Hash
Write-Output ("Wrote {0}" -f $dst)
Write-Output ("SHA256 {0}" -f $sha256)
