using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

try
{
    return Run(args);
}
catch (Exception ex)
{
    Console.Error.WriteLine($"fatal: {ex}");
    return 1;
}

static int Run(string[] args)
{
    if (args.Length == 0 || HasFlag(args, "--help") || HasFlag(args, "-h"))
    {
        PrintUsage();
        return 0;
    }

    var dllPath = GetOption(args, "--dll") ?? Defaults.DllPath;
    ConfigureNativeSearchPath(dllPath);
    RuntimeState.InitSettings = InitSettings.FromArgs(args);
    RuntimeState.TraceProtocol = HasFlag(args, "--trace-proto");

    var command = GetCommand(args);
    return command switch
    {
        "library-version" => RunLibraryVersion(),
        "list" => RunList(),
        "ms-list" => RunMassStorageList(args),
        "photos" => RunPhotos(args),
        "dump-fs" => RunDumpFs(args),
        "ms-dump" => RunMassStorageDump(args),
        "ms-resume" => RunMassStorageResume(args),
        "alive" => RunAlive(args),
        "device-version" => RunDeviceVersion(args),
        "version" => RunVersion(args),
        "read" => RunRead(args),
        _ => Fail($"unknown command '{command}'")
    };
}

static int RunLibraryVersion()
{
    var version = NativeMethods.GetLibraryVersion();
    Console.WriteLine(version ?? "<null>");
    return 0;
}

static int RunList()
{
    var devices = EnumerateDevices(out var initResult, out var countResult);

    Console.WriteLine($"init={initResult} count={countResult}");
    if (devices.Count == 0)
    {
        Console.WriteLine("no devices found");
        return 1;
    }

    foreach (var serial in devices)
    {
        var alive = NativeMethods.IsUsbDeviceAlive(serial);
        var deviceVersion = NativeMethods.TryGetDeviceVersion(serial, out var versionValue)
            ? $"0x{versionValue:x4}"
            : "<unavailable>";
        var version = NativeMethods.TryGetVersion(serial, out var versionString)
            ? versionString
            : "<unavailable>";

        Console.WriteLine($"{serial} alive={alive} deviceVersion={deviceVersion} version={version}");
    }

    return 0;
}

static int RunMassStorageList(string[] args)
{
    var id = MassStorageId.FromArgs(args);
    var volumes = EnumerateMassStorageVolumes(id, includeProbeFallback: true);

    Console.WriteLine($"ms vid=0x{id.Vid:x4} pid=0x{id.Pid:x4} rev=0x{id.Revision:x4} count={volumes.Count}");
    if (volumes.Count == 0)
    {
        Console.WriteLine("no mass-storage volumes found");
        return 1;
    }

    foreach (var volume in volumes)
    {
        var drive = volume.TrimEnd('\\');
        var infoText = MassStorageNativeMethods.TryGetUsbDeviceInfoEx(
            drive,
            out var deviceType,
            out var description,
            out var inquiryType,
            out var extra)
            ? $"type={deviceType} inquiryType={inquiryType} description={description} extra={extra}"
            : "info=<unavailable>";

        var physical = TryGetPhysicalDrivePathFromVolume(volume, out var physicalPath)
            ? physicalPath
            : "<unavailable>";

        Console.WriteLine($"{volume} physical={physical} {infoText}");
    }

    return 0;
}

static int RunPhotos(string[] args)
{
    IReadOnlyList<RemotePhotoInfo> found;
    try
    {
        using var client = PhotoProtocolClient.OpenFirstWithRetry();
        found = client.ListPhotos();
    }
    catch (Exception ex) when (!RuntimeState.TraceProtocol)
    {
        Console.Error.WriteLine($"direct photo query failed: {ex.Message}");
        found = Array.Empty<RemotePhotoInfo>();
    }

    if (found.Count == 0)
    {
        try
        {
            found = ListPhotosFromMassStorageMailbox(args);
        }
        catch (Exception ex) when (!RuntimeState.TraceProtocol)
        {
            Console.Error.WriteLine($"mailbox photo query failed: {ex.Message}");
            found = Array.Empty<RemotePhotoInfo>();
        }
    }

    foreach (var photo in found)
    {
        Console.WriteLine($"{photo.Path} size={photo.Size}");
    }

    Console.WriteLine($"photos count={found.Count}");
    return found.Count > 0 ? 0 : 1;
}

static int RunDumpFs(string[] args)
{
    var outputRoot = GetOption(args, "--output") ??
                     Path.Combine(Environment.CurrentDirectory, "vtech_fs_dump");
    Directory.CreateDirectory(outputRoot);

    var requestedRoots = GetMultiOption(args, "--root");
    IEnumerable<string> roots = requestedRoots.Count > 0
        ? requestedRoots
        : new[]
        {
            @"A:\",
            @"A:\DEFAULT",
            @"A:\BUNDLE",
            @"A:\PHO",
            @"\USENG"
        };

    using var client = PhotoProtocolClient.OpenFirstWithRetry();
    var queue = new Queue<string>(roots.Select(path => NormalizeRemoteDirectoryPath(path)));
    var seenDirectories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    var seenFiles = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    var failures = new List<string>();
    var dumpedFiles = 0;
    long dumpedBytes = 0;

    while (queue.Count > 0)
    {
        var directory = queue.Dequeue();
        if (!seenDirectories.Add(directory))
        {
            continue;
        }

        IReadOnlyList<RemoteFileEntry> entries;
        try
        {
            entries = client.ListDirectory(directory);
            Console.WriteLine($"dir {directory} entries={entries.Count}");
        }
        catch (Exception ex)
        {
            var message = $"dir {directory} failed: {ex.Message}";
            failures.Add(message);
            Console.Error.WriteLine(message);
            continue;
        }

        foreach (var entry in entries)
        {
            if (entry.IsDirectory)
            {
                queue.Enqueue(entry.Path);
                continue;
            }

            if (!entry.IsFile || !seenFiles.Add(entry.Path))
            {
                continue;
            }

            try
            {
                var data = client.ReadFile(entry.Path, entry.Size);
                var outputPath = RemotePathToLocalPath(outputRoot, entry.Path);
                Directory.CreateDirectory(Path.GetDirectoryName(outputPath) ?? outputRoot);
                File.WriteAllBytes(outputPath, data);
                dumpedFiles++;
                dumpedBytes += data.Length;
                Console.WriteLine($"file {entry.Path} size={data.Length} output={outputPath}");
            }
            catch (Exception ex)
            {
                var message = $"file {entry.Path} failed: {ex.Message}";
                failures.Add(message);
                Console.Error.WriteLine(message);
            }
        }
    }

    var manifestPath = Path.Combine(outputRoot, "_dump_manifest.txt");
    var manifestLines = new List<string>
    {
        $"files={dumpedFiles}",
        $"bytes={dumpedBytes}",
        $"failures={failures.Count}"
    };
    manifestLines.AddRange(failures);
    File.WriteAllLines(manifestPath, manifestLines);

    Console.WriteLine($"dump-fs files={dumpedFiles} bytes={dumpedBytes} failures={failures.Count} manifest={manifestPath}");
    return dumpedFiles > 0 ? 0 : 1;
}

static IReadOnlyList<RemotePhotoInfo> ListPhotosFromMassStorageMailbox(string[] args)
{
    var volume = GetOption(args, "--volume");
    if (string.IsNullOrWhiteSpace(volume))
    {
        var volumes = EnumerateMassStorageVolumes(MassStorageId.FromArgs(args), includeProbeFallback: true);
        if (volumes.Count == 0)
        {
            return Array.Empty<RemotePhotoInfo>();
        }

        volume = volumes[0];
    }

    if (!volume.EndsWith("\\", StringComparison.Ordinal))
    {
        volume += "\\";
    }

    if (TryGetPhysicalDrivePathFromVolume(volume, out var physicalPath))
    {
        return MailboxPhotoProtocolClient.ListPhotosFromPhysicalDrive(physicalPath);
    }

    return MailboxPhotoProtocolClient.ListPhotosFromVolume(volume);
}

static int RunMassStorageDump(string[] args)
{
    var id = MassStorageId.FromArgs(args);
    var source = (GetOption(args, "--source") ?? "volume").ToLowerInvariant();
    var volume = GetOption(args, "--volume");

    if (volume is null)
    {
        var volumes = EnumerateMassStorageVolumes(id);
        if (volumes.Count == 0)
        {
            return Fail("no mass-storage volumes found");
        }

        if (volumes.Count > 1)
        {
            return Fail("multiple mass-storage volumes found; pass --volume");
        }

        volume = volumes[0];
    }

    if (!volume.EndsWith("\\", StringComparison.Ordinal))
    {
        volume += "\\";
    }

    string targetPath;
    ulong totalLength;

    if (source == "physical")
    {
        if (!TryGetPhysicalDrivePathFromVolume(volume, out targetPath))
        {
            return Fail($"could not resolve physical drive for {volume}");
        }
    }
    else if (source == "volume")
    {
        targetPath = ToVolumeDevicePath(volume);
    }
    else
    {
        return Fail("source must be 'physical' or 'volume'");
    }

    if (!TryGetDeviceLength(targetPath, out totalLength))
    {
        return Fail($"could not query length for {targetPath}");
    }

    var bytesOverride = GetOption(args, "--bytes");
    if (bytesOverride is not null)
    {
        totalLength = Math.Min(totalLength, ParseUInt64(bytesOverride));
    }

    var outputPath = GetOption(args, "--output") ??
                     Path.Combine(
                         Environment.CurrentDirectory,
                         $"vtech_ms_{SanitizeForFileName(volume.TrimEnd('\\'))}_{source}.bin");

    Directory.CreateDirectory(Path.GetDirectoryName(outputPath) ?? Environment.CurrentDirectory);
    DumpDevice(targetPath, outputPath, totalLength);

    Console.WriteLine(
        $"dump ok source={source} volume={volume} target={targetPath} bytes=0x{totalLength:x} output={outputPath}");
    return 0;
}

static int RunMassStorageResume(string[] args)
{
    var id = MassStorageId.FromArgs(args);
    var volume = GetOption(args, "--volume");

    if (volume is null)
    {
        var volumes = EnumerateMassStorageVolumes(id);
        if (volumes.Count == 0)
        {
            return Fail("no mass-storage volumes found");
        }

        if (volumes.Count > 1)
        {
            return Fail("multiple mass-storage volumes found; pass --volume");
        }

        volume = volumes[0];
    }

    if (!volume.EndsWith("\\", StringComparison.Ordinal))
    {
        volume += "\\";
    }

    if (!TryReadFatVolumeInfo(volume, out var volumeInfo))
    {
        return Fail($"could not read FAT boot sector for {volume}");
    }

    var outputPath = GetOption(args, "--output") ??
                     Path.Combine(
                         Environment.CurrentDirectory,
                         $"vtech_ms_{SanitizeForFileName(volume.TrimEnd('\\'))}_resume.bin");
    var chunkBytes = ParseUInt32(GetOption(args, "--chunk-bytes") ?? "0x20000");
    var pauseMs = ParseInt32(GetOption(args, "--pause-ms") ?? "1000");
    var maxChunksOption = GetOption(args, "--max-chunks");
    var maxChunks = maxChunksOption is null ? int.MaxValue : ParseInt32(maxChunksOption);

    if (chunkBytes == 0)
    {
        return Fail("chunk-bytes must be greater than zero");
    }

    if (pauseMs < 0)
    {
        return Fail("pause-ms must be zero or greater");
    }

    Directory.CreateDirectory(Path.GetDirectoryName(outputPath) ?? Environment.CurrentDirectory);
    var existingLength = File.Exists(outputPath) ? new FileInfo(outputPath).Length : 0L;
    if (existingLength < 0 || (ulong)existingLength > volumeInfo.SizeBytes)
    {
        return Fail("existing output is larger than the expected volume size");
    }

    var offset = (ulong)existingLength;
    if (offset == volumeInfo.SizeBytes)
    {
        Console.WriteLine($"resume complete output={outputPath} bytes=0x{offset:x}");
        return 0;
    }

    var chunksCompleted = 0;
    while (offset < volumeInfo.SizeBytes && chunksCompleted < maxChunks)
    {
        var toRead = (int)Math.Min((ulong)chunkBytes, volumeInfo.SizeBytes - offset);
        if (!TryReadVolumeChunk(volume, offset, toRead, out var data, out var error))
        {
            Console.Error.WriteLine(
                $"resume stopped offset=0x{offset:x} remaining=0x{(volumeInfo.SizeBytes - offset):x} error={error}");
            return 1;
        }

        using (var output = new FileStream(outputPath, FileMode.Append, FileAccess.Write, FileShare.Read))
        {
            output.Write(data, 0, data.Length);
        }

        offset += (ulong)data.Length;
        chunksCompleted++;
        Console.WriteLine(
            $"chunk {chunksCompleted} wrote=0x{data.Length:x} offset=0x{offset:x}/0x{volumeInfo.SizeBytes:x}");

        if (offset < volumeInfo.SizeBytes && chunksCompleted < maxChunks && pauseMs > 0)
        {
            Thread.Sleep(pauseMs);
        }
    }

    Console.WriteLine($"resume status output={outputPath} bytes=0x{offset:x}/0x{volumeInfo.SizeBytes:x}");
    return offset == volumeInfo.SizeBytes ? 0 : 1;
}

static int RunAlive(string[] args)
{
    var serial = ResolveSerial(args);
    Console.WriteLine($"{serial} alive={NativeMethods.IsUsbDeviceAlive(serial)}");
    return 0;
}

static int RunDeviceVersion(string[] args)
{
    var serial = ResolveSerial(args);
    if (!NativeMethods.TryGetDeviceVersion(serial, out var version))
    {
        return Fail($"device-version failed for {serial}");
    }

    Console.WriteLine($"{serial} deviceVersion=0x{version:x4}");
    return 0;
}

static int RunVersion(string[] args)
{
    var serial = ResolveSerial(args);
    if (!NativeMethods.TryGetVersion(serial, out var version))
    {
        return Fail($"version failed for {serial}");
    }

    Console.WriteLine($"{serial} version={version}");
    return 0;
}

static int RunRead(string[] args)
{
    var serial = ResolveSerial(args);
    var address = ParseUInt32(GetOption(args, "--address") ?? "0x0");
    var length = ParseUInt32(GetOption(args, "--length") ?? "0x200");
    var chunkSize = ParseInt32(GetOption(args, "--mode") ?? "0x200");
    var outputPath = GetOption(args, "--output") ??
                     Path.Combine(
                         Environment.CurrentDirectory,
                         $"vtech_{SanitizeForFileName(serial)}_{address:x8}_{length:x}.bin");

    if (length == 0)
    {
        return Fail("length must be greater than zero");
    }

    if (chunkSize <= 0)
    {
        return Fail("mode must be greater than zero");
    }

    var openResult = NativeMethods.OpenUsbDevice(0, 0, serial);
    if (openResult != 1 && openResult != 3)
    {
        return Fail($"open failed for {serial} with code {openResult}");
    }

    var buffer = new byte[length];
    try
    {
        var readResult = NativeMethods.ReadFlash(serial, address, length, buffer, chunkSize);
        if (readResult != 1)
        {
            return Fail($"read failed for {serial} with code {readResult}");
        }

        Directory.CreateDirectory(Path.GetDirectoryName(outputPath) ?? Environment.CurrentDirectory);
        File.WriteAllBytes(outputPath, buffer);

        Console.WriteLine($"{serial} read ok address=0x{address:x8} length=0x{length:x} output={outputPath}");
        Console.WriteLine(HexPreview(buffer, 64));
        return 0;
    }
    finally
    {
        _ = NativeMethods.CloseUsbDevice(serial);
    }
}

static List<string> EnumerateDevices(out int initResult, out int countResult)
{
    var config = DeviceInitConfig.CreateDefault();
    initResult = NativeMethods.InitUsbDevices(ref config, IntPtr.Zero);
    countResult = NativeMethods.GetTotalUsbDeviceNumber(ref config);

    var devices = new List<string>();
    for (var index = 0; index < countResult; index++)
    {
        if (NativeMethods.TryFindDeviceSerial(index, out var serial))
        {
            devices.Add(serial);
        }
    }

    return devices;
}

static List<string> EnumerateMassStorageVolumes(MassStorageId id, bool includeProbeFallback = false)
{
    var count = MassStorageNativeMethods.GetTotalMsUsbDeviceNumber(ref id);
    var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    var volumes = new List<string>();
    for (var index = 0; index < count; index++)
    {
        var buffer = new StringBuilder(64);
        var result = MassStorageNativeMethods.GetMsUsbDeviceVolume(ref id, index, buffer);
        if (result != 0)
        {
            var value = buffer.ToString();
            if (!string.IsNullOrWhiteSpace(value))
            {
                if (!value.EndsWith("\\", StringComparison.Ordinal))
                {
                    value += "\\";
                }

                if (seen.Add(value))
                {
                    volumes.Add(value);
                }
            }
        }
    }

    if (includeProbeFallback)
    {
        foreach (var candidate in ProbeMassStorageVolumes())
        {
            if (seen.Add(candidate))
            {
                volumes.Add(candidate);
            }
        }
    }

    return volumes;
}

static IEnumerable<string> ProbeMassStorageVolumes()
{
    foreach (var drive in DriveInfo.GetDrives().OrderBy(static drive => drive.Name, StringComparer.OrdinalIgnoreCase))
    {
        if (!drive.IsReady)
        {
            continue;
        }

        var root = drive.RootDirectory.FullName.TrimEnd('\\');
        if (!MassStorageNativeMethods.TryGetUsbDeviceInfoEx(
                root,
                out _,
                out var description,
                out _,
                out var extra))
        {
            continue;
        }

        if (!LooksLikeVTechMassStorage(description, extra))
        {
            continue;
        }

        yield return root + "\\";
    }
}

static bool LooksLikeVTechMassStorage(string description, string extra)
{
    return description.Contains("VTECH", StringComparison.OrdinalIgnoreCase) ||
           description.Contains("USB-MSDC", StringComparison.OrdinalIgnoreCase) ||
           extra.Contains("VTECH", StringComparison.OrdinalIgnoreCase) ||
           extra.Contains("USB-MSDC", StringComparison.OrdinalIgnoreCase);
}

static bool TryGetPhysicalDrivePathFromVolume(string volume, out string physicalPath)
{
    using var handle = OpenReadHandle(ToVolumeDevicePath(volume));
    if (handle.IsInvalid || !KernelNativeMethods.TryGetStorageDeviceNumber(handle, out var deviceNumber))
    {
        physicalPath = string.Empty;
        return false;
    }

    physicalPath = $@"\\.\PhysicalDrive{deviceNumber.DeviceNumber}";
    return true;
}

static bool TryGetDeviceLength(string path, out ulong length)
{
    using var handle = OpenReadHandle(path);
    if (handle.IsInvalid)
    {
        length = 0;
        return false;
    }

    return KernelNativeMethods.TryGetLengthInfo(handle, out length);
}

static bool TryReadFatVolumeInfo(string volume, out FatVolumeInfo info)
{
    using var handle = OpenReadHandle(ToVolumeDevicePath(volume));
    if (handle.IsInvalid)
    {
        info = default;
        return false;
    }

    using var input = new FileStream(handle, FileAccess.Read, 4096, false);
    var boot = new byte[512];
    var read = input.Read(boot, 0, boot.Length);
    if (read < boot.Length)
    {
        info = default;
        return false;
    }

    var bytesPerSector = BitConverter.ToUInt16(boot, 11);
    var totalSectors16 = BitConverter.ToUInt16(boot, 19);
    var totalSectors32 = BitConverter.ToUInt32(boot, 32);
    var totalSectors = totalSectors16 != 0 ? totalSectors16 : totalSectors32;
    if (bytesPerSector == 0 || totalSectors == 0)
    {
        info = default;
        return false;
    }

    var oem = Encoding.ASCII.GetString(boot, 3, 8).TrimEnd('\0', ' ');
    var fsType = Encoding.ASCII.GetString(boot, 54, 8).TrimEnd('\0', ' ');
    info = new FatVolumeInfo(oem, fsType, bytesPerSector, totalSectors, (ulong)bytesPerSector * totalSectors);
    return true;
}

static bool TryReadVolumeChunk(string volume, ulong offset, int length, out byte[] data, out string error)
{
    using var handle = OpenReadHandle(ToVolumeDevicePath(volume));
    if (handle.IsInvalid)
    {
        data = Array.Empty<byte>();
        error = $"open failed for {volume}";
        return false;
    }

    try
    {
        using var input = new FileStream(handle, FileAccess.Read, 1024 * 1024, false);
        input.Position = (long)offset;
        data = new byte[length];
        var totalRead = 0;
        while (totalRead < length)
        {
            var read = input.Read(data, totalRead, length - totalRead);
            if (read <= 0)
            {
                break;
            }

            totalRead += read;
        }

        if (totalRead != length)
        {
            Array.Resize(ref data, totalRead);
            error = $"short read {totalRead}/{length}";
            return false;
        }

        error = string.Empty;
        return true;
    }
    catch (Exception ex)
    {
        data = Array.Empty<byte>();
        error = ex.Message;
        return false;
    }
}

static void DumpDevice(string path, string outputPath, ulong length)
{
    using var inputHandle = OpenReadHandle(path);
    if (inputHandle.IsInvalid)
    {
        throw new InvalidOperationException($"failed to open {path}");
    }

    using var input = new FileStream(inputHandle, FileAccess.Read, 1024 * 1024, false);
    using var output = new FileStream(outputPath, FileMode.Create, FileAccess.Write, FileShare.Read);

    var buffer = new byte[1024 * 1024];
    var remaining = length;
    while (remaining > 0)
    {
        var requested = (int)Math.Min((ulong)buffer.Length, remaining);
        var read = input.Read(buffer, 0, requested);
        if (read <= 0)
        {
            break;
        }

        output.Write(buffer, 0, read);
        remaining -= (ulong)read;
    }
}

static SafeFileHandle OpenReadHandle(string path)
{
    return KernelNativeMethods.CreateFile(
        path,
        KernelNativeMethods.GenericRead,
        KernelNativeMethods.FileShareRead | KernelNativeMethods.FileShareWrite,
        IntPtr.Zero,
        KernelNativeMethods.OpenExisting,
        0,
        IntPtr.Zero);
}

static string ToVolumeDevicePath(string volume)
{
    return $@"\\.\{volume.TrimEnd('\\')}";
}

static string ResolveSerial(string[] args)
{
    var serial = GetOption(args, "--serial");
    if (!string.IsNullOrWhiteSpace(serial))
    {
        return serial;
    }

    var devices = EnumerateDevices(out _, out _);
    return devices.Count switch
    {
        0 => throw new InvalidOperationException("no devices found"),
        1 => devices[0],
        _ => throw new InvalidOperationException("multiple devices found; pass --serial")
    };
}

static string GetCommand(string[] args)
{
    foreach (var arg in args)
    {
        if (!arg.StartsWith("-"))
        {
            return arg;
        }
    }

    throw new InvalidOperationException("missing command");
}

static string? GetOption(string[] args, string name)
{
    for (var i = 0; i < args.Length - 1; i++)
    {
        if (string.Equals(args[i], name, StringComparison.OrdinalIgnoreCase))
        {
            return args[i + 1];
        }
    }

    return null;
}

static List<string> GetMultiOption(string[] args, string name)
{
    var values = new List<string>();
    for (var i = 0; i < args.Length - 1; i++)
    {
        if (string.Equals(args[i], name, StringComparison.OrdinalIgnoreCase))
        {
            values.Add(args[i + 1]);
        }
    }

    return values;
}

static bool HasFlag(string[] args, string flag)
{
    return args.Any(arg => string.Equals(arg, flag, StringComparison.OrdinalIgnoreCase));
}

static string NormalizeRemoteDirectoryPath(string path)
{
    var normalized = path.Replace('/', '\\').Trim();
    if (normalized.Length == 0)
    {
        return @"A:\";
    }

    if (string.Equals(normalized, "A:", StringComparison.OrdinalIgnoreCase))
    {
        return @"A:\";
    }

    if (string.Equals(normalized, @"A:\", StringComparison.OrdinalIgnoreCase))
    {
        return @"A:\";
    }

    if (normalized.StartsWith(@"A:\", StringComparison.OrdinalIgnoreCase))
    {
        return normalized.TrimEnd('\\');
    }

    if (normalized.StartsWith(@"\", StringComparison.Ordinal))
    {
        return normalized.TrimEnd('\\');
    }

    return normalized.TrimEnd('\\');
}

static string RemotePathToLocalPath(string outputRoot, string remotePath)
{
    var normalized = remotePath.Replace('/', '\\').Trim();
    string[] segments;

    if (normalized.StartsWith(@"A:\", StringComparison.OrdinalIgnoreCase))
    {
        segments = new[] { "A" }
            .Concat(normalized[3..].Split('\\', StringSplitOptions.RemoveEmptyEntries))
            .ToArray();
    }
    else if (string.Equals(normalized, @"A:\", StringComparison.OrdinalIgnoreCase))
    {
        segments = new[] { "A" };
    }
    else if (normalized.StartsWith(@"\", StringComparison.Ordinal))
    {
        segments = new[] { "_root" }
            .Concat(normalized.TrimStart('\\').Split('\\', StringSplitOptions.RemoveEmptyEntries))
            .ToArray();
    }
    else
    {
        segments = new[] { "_other" }
            .Concat(normalized.Split('\\', StringSplitOptions.RemoveEmptyEntries))
            .ToArray();
    }

    return Path.Combine(new[] { outputRoot }.Concat(segments).ToArray());
}

static uint ParseUInt32(string value)
{
    return value.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
        ? Convert.ToUInt32(value[2..], 16)
        : Convert.ToUInt32(value, 10);
}

static ulong ParseUInt64(string value)
{
    return value.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
        ? Convert.ToUInt64(value[2..], 16)
        : Convert.ToUInt64(value, 10);
}

static int ParseInt32(string value)
{
    return value.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
        ? Convert.ToInt32(value[2..], 16)
        : Convert.ToInt32(value, 10);
}

static string HexPreview(byte[] data, int maxBytes)
{
    var count = Math.Min(data.Length, maxBytes);
    var builder = new StringBuilder(count * 3);
    for (var i = 0; i < count; i++)
    {
        if (i > 0)
        {
            builder.Append(' ');
        }

        builder.Append(data[i].ToString("x2"));
    }

    return builder.ToString();
}

static string SanitizeForFileName(string input)
{
    var invalid = Path.GetInvalidFileNameChars();
    var builder = new StringBuilder(input.Length);
    foreach (var ch in input)
    {
        builder.Append(invalid.Contains(ch) ? '_' : ch);
    }

    return builder.ToString();
}

static void ConfigureNativeSearchPath(string dllPath)
{
    if (!File.Exists(dllPath))
    {
        throw new FileNotFoundException("DLL not found", dllPath);
    }

    var directory = Path.GetDirectoryName(dllPath);
    if (string.IsNullOrWhiteSpace(directory))
    {
        throw new InvalidOperationException("DLL directory is empty");
    }

    if (!NativeMethods.SetDllDirectory(directory))
    {
        throw new InvalidOperationException($"SetDllDirectory failed for {directory}");
    }
}

static int Fail(string message)
{
    Console.Error.WriteLine(message);
    return 1;
}

static void PrintUsage()
{
    Console.WriteLine(
        """
        vtech-usb-cli

        Commands:
          library-version
          list
          ms-list [--vid 0x0F88] [--pid 0x2D40] [--rev 0xFFFF]
          photos [--volume D:]
          dump-fs [--output <dir>] [--root <remote-path>]
          ms-dump [--vid 0x0F88] [--pid 0x2D40] [--rev 0xFFFF] [--volume D:] [--source volume|physical] [--bytes 0x1000] [--output <path>]
          ms-resume [--vid 0x0F88] [--pid 0x2D40] [--rev 0xFFFF] [--volume D:] [--chunk-bytes 0x20000] [--pause-ms 1000] [--max-chunks 1] [--output <path>]
          alive --serial <serial>
          device-version --serial <serial>
          version --serial <serial>
          read --serial <serial> [--address 0x0] [--length 0x200] [--mode 0x200] [--output <path>]

        Global options:
          --dll <path>    Override the DLL path.
          --u0 <value>    Override initializer field 0.
          --u1 <value>    Override initializer field 1.
          --u2 <value>    Override initializer field 2.
          --device-class <value>
          --volume-marker <value>
          --vid <value>   Override mass-storage VID.
          --pid <value>   Override mass-storage PID.
          --rev <value>   Override mass-storage revision.
          --chunk-bytes <value>
          --pause-ms <value>
          --max-chunks <value>
          --root <path>  Repeatable for dump-fs.

        Notes:
          - The default USB initializer uses USBSTOR\Disk, VLINK_FILE_SYSTEM, and the three MobiGo product IDs from the bundled app config.
          - The mass-storage path defaults to VID 0x0F88, PID 0x2D40, REV 0xFFFF and dumps from the logical volume path.
          - This CLI does not expose any write command.
        """);
}

[StructLayout(LayoutKind.Sequential, Pack = 1, CharSet = CharSet.Unicode)]
internal unsafe struct DeviceInitConfig
{
    public ushort Unknown0;
    public ushort Unknown1;
    public ushort Unknown2;
    public fixed char DeviceClass[17];
    public fixed char VolumeMarker[64];

    public static DeviceInitConfig CreateDefault()
    {
        var config = new DeviceInitConfig
        {
            Unknown0 = RuntimeState.InitSettings.Unknown0,
            Unknown1 = RuntimeState.InitSettings.Unknown1,
            Unknown2 = RuntimeState.InitSettings.Unknown2
        };

        config.SetDeviceClass(RuntimeState.InitSettings.DeviceClass);
        config.SetVolumeMarker(RuntimeState.InitSettings.VolumeMarker);
        return config;
    }

    private void SetDeviceClass(string value)
    {
        fixed (char* destination = DeviceClass)
        {
            CopyNullTerminated(value, destination, 17);
        }
    }

    private void SetVolumeMarker(string value)
    {
        fixed (char* destination = VolumeMarker)
        {
            CopyNullTerminated(value, destination, 64);
        }
    }

    private static void CopyNullTerminated(string value, char* destination, int capacity)
    {
        var count = Math.Min(value.Length, capacity - 1);
        for (var index = 0; index < count; index++)
        {
            destination[index] = value[index];
        }

        for (var index = count; index < capacity; index++)
        {
            destination[index] = '\0';
        }
    }
}

[StructLayout(LayoutKind.Sequential, Pack = 1)]
internal struct MassStorageId
{
    public ushort Vid;
    public ushort Pid;
    public ushort Revision;

    internal static MassStorageId FromArgs(string[] args)
    {
        return new MassStorageId
        {
            Vid = GetUShort(args, "--vid", Defaults.MassStorageVid),
            Pid = GetUShort(args, "--pid", Defaults.MassStoragePid),
            Revision = GetUShort(args, "--rev", Defaults.MassStorageRev)
        };
    }

    private static ushort GetUShort(string[] args, string name, ushort fallback)
    {
        var value = CliParsing.GetOption(args, name);
        return value is null ? fallback : checked((ushort)CliParsing.ParseUInt32(value));
    }
}

internal readonly record struct InitSettings(
    ushort Unknown0,
    ushort Unknown1,
    ushort Unknown2,
    string DeviceClass,
    string VolumeMarker)
{
    internal static InitSettings FromArgs(string[] args)
    {
        return new InitSettings(
            GetUShortOption(args, "--u0", Defaults.ProductId0),
            GetUShortOption(args, "--u1", Defaults.ProductId1),
            GetUShortOption(args, "--u2", Defaults.ProductId2),
            CliParsing.GetOption(args, "--device-class") ?? Defaults.DeviceClass,
            CliParsing.GetOption(args, "--volume-marker") ?? Defaults.VolumeMarker);
    }

    private static ushort GetUShortOption(string[] args, string name, ushort fallback)
    {
        var value = CliParsing.GetOption(args, name);
        return value is null ? fallback : checked((ushort)CliParsing.ParseUInt32(value));
    }
}

internal static class Defaults
{
    internal const string DllPath = @"C:\Users\Max\Desktop\VTech\DownloadManager\System\VTech2010USBDllU.dll";
    internal const string DeviceClass = @"USBSTOR\Disk";
    internal const string VolumeMarker = "VLINK_FILE_SYSTEM";
    internal const ushort ProductId0 = 1158;
    internal const ushort ProductId1 = 11583;
    internal const ushort ProductId2 = 11584;
    internal const ushort MassStorageVid = 0x0F88;
    internal const ushort MassStoragePid = 0x2D40;
    internal const ushort MassStorageRev = 0xFFFF;
}

internal static class RuntimeState
{
    internal static InitSettings InitSettings = new(
        Defaults.ProductId0,
        Defaults.ProductId1,
        Defaults.ProductId2,
        Defaults.DeviceClass,
        Defaults.VolumeMarker);

    internal static bool TraceProtocol;
}

internal static class CliParsing
{
    internal static string? GetOption(string[] args, string name)
    {
        for (var i = 0; i < args.Length - 1; i++)
        {
            if (string.Equals(args[i], name, StringComparison.OrdinalIgnoreCase))
            {
                return args[i + 1];
            }
        }

        return null;
    }

    internal static uint ParseUInt32(string value)
    {
        return value.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
            ? Convert.ToUInt32(value[2..], 16)
            : Convert.ToUInt32(value, 10);
    }
}

[StructLayout(LayoutKind.Sequential)]
internal struct StorageDeviceNumber
{
    public uint DeviceType;
    public uint DeviceNumber;
    public uint PartitionNumber;
}

[StructLayout(LayoutKind.Sequential)]
internal struct GetLengthInformation
{
    public long Length;
}

internal readonly record struct FatVolumeInfo(
    string Oem,
    string FileSystemType,
    ushort BytesPerSector,
    uint TotalSectors,
    ulong SizeBytes);

internal readonly record struct RemotePhotoInfo(
    string Name,
    string Path,
    int Size);

internal readonly record struct RemoteFileInfo(
    int Kind,
    int Size,
    uint Handle,
    uint RawKind)
{
    internal bool Exists => Kind != 0;
    internal bool IsFile => Kind == 1;
    internal bool IsDirectory => Kind == 2;
}

internal readonly record struct RemoteFileEntry(
    string Name,
    string Path,
    int Kind,
    int Size,
    uint Timestamp,
    uint Attributes)
{
    internal bool IsFile => Kind == 1;
    internal bool IsDirectory => Kind == 2;
}

internal readonly record struct PhotoDirectoryEntry(
    int Cursor,
    string Name,
    int Size,
    uint Timestamp,
    uint Attributes);

internal sealed class PhotoProtocolClient : IDisposable
{
    private const uint MailboxOffset = 0x280000;
    private const int PacketSize = 0x200;
    private const int DirectoryEntrySize = 28;
    private const int PathFieldBytes = 42;
    private const ushort OpenModeRead = 1;
    private readonly string? _serial;
    private readonly byte[]? _context;

    private PhotoProtocolClient(string serial)
    {
        _serial = serial;
        _context = null;
    }

    private PhotoProtocolClient(byte[] context)
    {
        _context = context;
        _serial = null;
    }

    internal static PhotoProtocolClient OpenFirst()
    {
        try
        {
            var candidate = LsTransportNativeMethods.BuildCandidate(Defaults.MassStorageVid, Defaults.MassStoragePid);
            _ = LsTransportNativeMethods.InitUsbDevices(candidate, 1);
            _ = LsTransportNativeMethods.GetTotalUsbDeviceNumber(candidate);
            var context = new byte[64];
            var findResult = LsTransportNativeMethods.FindDeviceSerial(context, 0);
            if (findResult == 1)
            {
                var error = -1;
                var candidateOpenResult = LsTransportNativeMethods.OpenUsbDevice(candidate, context, ref error);
                if (candidateOpenResult == 1)
                {
                    return new PhotoProtocolClient(context);
                }
            }
        }
        catch
        {
        }

        var config = DeviceInitConfig.CreateDefault();
        var initResult = NativeMethods.InitUsbDevices(ref config, IntPtr.Zero);
        if (initResult != 1)
        {
            throw new InvalidOperationException($"DLL_LSInitUSBDevices failed with code {initResult}");
        }

        var count = NativeMethods.GetTotalUsbDeviceNumber(ref config);
        if (count < 1)
        {
            throw new InvalidOperationException("no matching VTech device found");
        }

        if (!NativeMethods.TryFindDeviceSerial(0, out var serial))
        {
            throw new InvalidOperationException("DLL_LSFindDevSN failed");
        }

        var openResult = NativeMethods.OpenUsbDevice(0, 0, serial);
        if (openResult != 1 && openResult != 3)
        {
            throw new InvalidOperationException($"DLL_LSOpenUSBDevice failed with rc={openResult}");
        }

        return new PhotoProtocolClient(serial);
    }

    internal static PhotoProtocolClient OpenFirstWithRetry()
    {
        Exception? last = null;
        for (var attempt = 0; attempt < 5; attempt++)
        {
            try
            {
                return OpenFirst();
            }
            catch (Exception ex)
            {
                last = ex;
                Thread.Sleep(150);
            }
        }

        throw new InvalidOperationException("failed to open VTech device after retries", last);
    }

    internal IReadOnlyList<RemotePhotoInfo> ListPhotos()
    {
        InitializePhotoBrowse();

        var photos = new List<RemotePhotoInfo>();
        var seenNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var seenPages = new HashSet<string>(StringComparer.Ordinal);

        var reply = Send(BuildPathCommand(0x06, @"A:\PHO", 0x1f));
        while (true)
        {
            var pageKey = Convert.ToHexString(reply);
            if (!seenPages.Add(pageKey))
            {
                break;
            }

            var page = ParseDirectoryPage(reply);
            foreach (var entry in page.Entries)
            {
                if (!entry.Name.EndsWith(".PHO", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                if (!seenNames.Add(entry.Name))
                {
                    continue;
                }

                var path = $@"A:\PHO\{entry.Name}";
                photos.Add(new RemotePhotoInfo(entry.Name, path, entry.Size));
            }

            if (page.ReachedEnd || page.NextCursor < 0)
            {
                break;
            }

            reply = Send(BuildSimpleCommand(0x07, checked((uint)page.NextCursor)));
        }

        return photos;
    }

    internal int PathType(string path)
    {
        var reply = Send(BuildPathCommand(0x10, path, PathFieldBytes));
        var rawKind = BitConverter.ToUInt32(reply, 0);
        return (int)(rawKind & 0xFFFF);
    }

    internal RemoteFileInfo Stat(string path)
    {
        var typeReply = Send(BuildPathCommand(0x10, path, PathFieldBytes));
        var rawKind = BitConverter.ToUInt32(typeReply, 0);
        var kind = (int)(rawKind & 0xFFFF);
        if (kind == 0)
        {
            return new RemoteFileInfo(0, 0, 0, rawKind);
        }

        var statReply = Send(BuildPathCommand(0x09, path, PathFieldBytes));
        var size = BitConverter.ToInt32(statReply, 4);
        return new RemoteFileInfo(kind, size, 0, rawKind);
    }

    internal IReadOnlyList<RemoteFileEntry> ListDirectory(string path)
    {
        if (path.StartsWith(@"A:", StringComparison.OrdinalIgnoreCase))
        {
            InitializePhotoBrowse();
        }

        var entries = new List<RemoteFileEntry>();
        var seenNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var seenPages = new HashSet<string>(StringComparer.Ordinal);
        var reply = Send(BuildPathCommand(0x06, path, 0x1f));
        var sawEntry = false;

        while (true)
        {
            var pageKey = Convert.ToHexString(reply);
            if (!seenPages.Add(pageKey))
            {
                break;
            }

            var page = ParseDirectoryPage(reply);
            foreach (var entry in page.Entries)
            {
                if (entry.Name is "." or "..")
                {
                    continue;
                }

                if (!seenNames.Add(entry.Name))
                {
                    continue;
                }

                var childPath = CombineRemotePath(path, entry.Name);
                var kind = GuessEntryKind(entry.Attributes, childPath);
                entries.Add(new RemoteFileEntry(entry.Name, childPath, kind, entry.Size, entry.Timestamp, entry.Attributes));
                sawEntry = true;
            }

            if (page.ReachedEnd || page.NextCursor < 0)
            {
                break;
            }

            reply = Send(BuildCommand(0x07, checked((uint)page.NextCursor)));
        }

        if (!sawEntry)
        {
            var kind = PathType(path);
            if (kind != 2)
            {
                throw new InvalidOperationException($"{path} is not a directory");
            }
        }

        return entries;
    }

    internal byte[] ReadFile(string path, int? expectedSize = null)
    {
        var info = OpenForRead(path);
        if (!info.IsFile)
        {
            throw new InvalidOperationException($"{path} is not a file");
        }

        var length = expectedSize.GetValueOrDefault(info.Size);
        if (length <= 0)
        {
            length = info.Size;
        }

        try
        {
            var output = new byte[length];
            var offset = 0;
            while (offset < length)
            {
                var chunk = Math.Min(PacketSize, length - offset);
                var reply = Send(BuildCommand(0x03, info.Handle, PacketSize), PacketSize);
                Array.Copy(reply, 0, output, offset, chunk);
                offset += chunk;
            }

            return output;
        }
        finally
        {
            _ = Send(BuildCommand(0x05, info.Handle));
        }
    }

    public void Dispose()
    {
        if (_context is not null)
        {
            _ = LsTransportNativeMethods.CloseUsbDevice(_context);
            return;
        }

        if (_serial is not null)
        {
            _ = NativeMethods.CloseUsbDevice(_serial);
        }
    }

    private void InitializePhotoBrowse()
    {
        _ = Send(BuildCommand(0x01));
        _ = Send(BuildCommand(0x18));
        _ = Send(BuildCommand(0x14, 'A'));
        _ = Send(BuildCommand(0x17, 'A'));
    }

    private RemoteFileInfo OpenForRead(string path)
    {
        var stat = Stat(path);
        if (!stat.IsFile)
        {
            return stat;
        }

        var reply = Send(BuildOpenCommand(path, OpenModeRead));
        var handle = BitConverter.ToUInt32(reply, 0);
        var size = BitConverter.ToInt32(reply, 4);
        if (stat.Size > 0 && size != stat.Size)
        {
            size = stat.Size;
        }

        return new RemoteFileInfo(1, size, handle, stat.RawKind);
    }

    private byte[] Send(byte[] request, int replyLength = PacketSize)
    {
        var opcode = BitConverter.ToUInt32(request, 0);
        byte[] reply;

        if (_context is not null)
        {
            var writeResult = LsTransportNativeMethods.WriteFlash(_context, MailboxOffset, request);
            if (writeResult != 1)
            {
                throw new InvalidOperationException(
                    $"DLL_LSWriteFlash failed with code {writeResult} for opcode 0x{opcode:x2}");
            }

            if (!LsTransportNativeMethods.TryReadFlash(_context, MailboxOffset, replyLength, out reply))
            {
                throw new InvalidOperationException(
                    $"DLL_LSReadFlash failed for opcode 0x{opcode:x2}");
            }
        }
        else
        {
            var writeResult = NativeMethods.WriteFlash(_serial!, MailboxOffset, (uint)request.Length, request, request.Length);
            if (writeResult != 1)
            {
                throw new InvalidOperationException(
                    $"DLL_LSWriteFlash failed with code {writeResult} for opcode 0x{opcode:x2}");
            }

            reply = new byte[replyLength];
            if (NativeMethods.ReadFlash(_serial!, MailboxOffset, (uint)replyLength, reply, replyLength) != 1)
            {
                throw new InvalidOperationException(
                    $"DLL_LSReadFlash failed for opcode 0x{opcode:x2}");
            }
        }

        if (RuntimeState.TraceProtocol)
        {
            Console.Error.WriteLine($"proto op=0x{opcode:x2} req={HexPreviewLocal(request, 32)}");
            Console.Error.WriteLine($"proto op=0x{opcode:x2} rsp={HexPreviewLocal(reply, 64)}");
        }

        return reply;
    }

    private static PhotoDirectoryPage ParseDirectoryPage(byte[] reply)
    {
        var entries = new List<PhotoDirectoryEntry>();
        var reachedEnd = false;
        var nextCursor = -1;

        for (var offset = 0; offset + DirectoryEntrySize <= reply.Length; offset += DirectoryEntrySize)
        {
            var rawCursor = BitConverter.ToInt32(reply, offset);
            if ((short)rawCursor == -1 || rawCursor == 0)
            {
                reachedEnd = true;
                break;
            }

            var name = ReadAsciiZ(reply, offset + 4, 12);
            if (string.IsNullOrWhiteSpace(name))
            {
                reachedEnd = true;
                break;
            }

            var attributes = BitConverter.ToUInt32(reply, offset + 16);
            var timestamp = BitConverter.ToUInt32(reply, offset + 20);
            var size = BitConverter.ToInt32(reply, offset + 24);
            entries.Add(new PhotoDirectoryEntry(rawCursor, name, size, timestamp, attributes));
            nextCursor = rawCursor;
        }

        return new PhotoDirectoryPage(entries, nextCursor, reachedEnd);
    }

    private static byte[] BuildSimpleCommand(uint opcode, uint argument = 0)
    {
        var request = new byte[PacketSize];
        BitConverter.GetBytes(opcode).CopyTo(request, 0);
        BitConverter.GetBytes(argument).CopyTo(request, 4);
        return request;
    }

    private static byte[] BuildCommand(uint opcode, params uint[] arguments)
    {
        var request = new byte[PacketSize];
        BitConverter.GetBytes(opcode).CopyTo(request, 0);
        for (var i = 0; i < arguments.Length; i++)
        {
            BitConverter.GetBytes(arguments[i]).CopyTo(request, 4 + (i * 4));
        }

        return request;
    }

    private static byte[] BuildPathCommand(uint opcode, string path, int pathFieldLength)
    {
        var request = new byte[PacketSize];
        BitConverter.GetBytes(opcode).CopyTo(request, 0);
        var ascii = Encoding.ASCII.GetBytes(path.Replace('/', '\\'));
        var count = Math.Min(ascii.Length, pathFieldLength - 1);
        Array.Copy(ascii, 0, request, 4, count);
        request[4 + count] = 0;
        return request;
    }

    private static byte[] BuildOpenCommand(string path, ushort mode)
    {
        var request = new byte[PacketSize];
        BitConverter.GetBytes((uint)0x02).CopyTo(request, 0);
        var ascii = Encoding.ASCII.GetBytes(path.Replace('/', '\\'));
        var count = Math.Min(ascii.Length, PathFieldBytes - 1);
        Array.Copy(ascii, 0, request, 4, count);
        request[4 + count] = 0;
        BitConverter.GetBytes(mode).CopyTo(request, 4 + PathFieldBytes);
        return request;
    }

    private static int GuessEntryKind(uint attributes, string path)
    {
        if ((attributes & 0x10) != 0)
        {
            return 2;
        }

        return path.Contains('.', StringComparison.Ordinal) ? 1 : 2;
    }

    private static string ReadAsciiZ(byte[] buffer, int offset, int maxLength)
    {
        var count = 0;
        while (count < maxLength && buffer[offset + count] != 0)
        {
            count++;
        }

        return Encoding.ASCII.GetString(buffer, offset, count);
    }

    private static string HexPreviewLocal(byte[] data, int maxBytes)
    {
        var count = Math.Min(data.Length, maxBytes);
        var builder = new StringBuilder(count * 3);
        for (var i = 0; i < count; i++)
        {
            if (i > 0)
            {
                builder.Append(' ');
            }

            builder.Append(data[i].ToString("x2"));
        }

        return builder.ToString();
    }

    private readonly record struct PhotoDirectoryPage(
        IReadOnlyList<PhotoDirectoryEntry> Entries,
        int NextCursor,
        bool ReachedEnd);

    private static string CombineRemotePath(string directory, string name)
    {
        var normalizedDirectory = directory.Replace('/', '\\').TrimEnd('\\');
        if (normalizedDirectory == "A:")
        {
            return $@"A:\{name}";
        }

        if (normalizedDirectory.Length == 0)
        {
            return $@"\{name}";
        }

        return normalizedDirectory.StartsWith(@"\", StringComparison.Ordinal)
            ? $@"{normalizedDirectory}\{name}"
            : $@"{normalizedDirectory}\{name}";
    }
}

internal static class MailboxPhotoProtocolClient
{
    private const int PacketSize = 0x200;
    private const int DirectoryEntrySize = 28;

    internal static IReadOnlyList<RemotePhotoInfo> ListPhotosFromVolume(string volume)
    {
        using var mailbox = MailboxVolume.Open(volume);
        return ListPhotos(mailbox);
    }

    internal static IReadOnlyList<RemotePhotoInfo> ListPhotosFromPhysicalDrive(string physicalPath)
    {
        using var mailbox = MailboxVolume.OpenPhysical(physicalPath);
        return ListPhotos(mailbox);
    }

    private static IReadOnlyList<RemotePhotoInfo> ListPhotos(MailboxVolume mailbox)
    {
        _ = mailbox.SendCommand(BuildSimpleCommand(0x01));
        _ = mailbox.SendCommand(BuildSimpleCommand(0x18));
        _ = mailbox.SendCommand(BuildSimpleCommand(0x14, 'A'));
        _ = mailbox.SendCommand(BuildSimpleCommand(0x17, 'A'));

        var photos = new List<RemotePhotoInfo>();
        var seenNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var seenPages = new HashSet<string>(StringComparer.Ordinal);

        var reply = mailbox.SendCommand(BuildPathCommand(0x06, @"A:\PHO", 0x1f));
        while (true)
        {
            var pageKey = Convert.ToHexString(reply);
            if (!seenPages.Add(pageKey))
            {
                break;
            }

            var page = ParseDirectoryPage(reply);
            foreach (var entry in page.Entries)
            {
                if (!entry.Name.EndsWith(".PHO", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                if (!seenNames.Add(entry.Name))
                {
                    continue;
                }

                photos.Add(new RemotePhotoInfo(entry.Name, $@"A:\PHO\{entry.Name}", entry.Size));
            }

            if (page.ReachedEnd || page.NextCursor < 0)
            {
                break;
            }

            reply = mailbox.SendCommand(BuildSimpleCommand(0x07, checked((uint)page.NextCursor)));
        }

        return photos;
    }

    private static PhotoDirectoryPage ParseDirectoryPage(byte[] reply)
    {
        var entries = new List<PhotoDirectoryEntry>();
        var reachedEnd = false;
        var nextCursor = -1;

        for (var offset = 0; offset + DirectoryEntrySize <= reply.Length; offset += DirectoryEntrySize)
        {
            var rawCursor = BitConverter.ToInt32(reply, offset);
            if ((short)rawCursor == -1 || rawCursor == 0)
            {
                reachedEnd = true;
                break;
            }

            var name = ReadAsciiZ(reply, offset + 4, 12);
            if (string.IsNullOrWhiteSpace(name))
            {
                reachedEnd = true;
                break;
            }

            var attributes = BitConverter.ToUInt32(reply, offset + 16);
            var timestamp = BitConverter.ToUInt32(reply, offset + 20);
            var size = BitConverter.ToInt32(reply, offset + 24);
            entries.Add(new PhotoDirectoryEntry(rawCursor, name, size, timestamp, attributes));
            nextCursor = rawCursor;
        }

        return new PhotoDirectoryPage(entries, nextCursor, reachedEnd);
    }

    private static byte[] BuildSimpleCommand(uint opcode, uint argument = 0)
    {
        var request = new byte[PacketSize];
        BitConverter.GetBytes(opcode).CopyTo(request, 0);
        BitConverter.GetBytes(argument).CopyTo(request, 4);
        return request;
    }

    private static byte[] BuildPathCommand(uint opcode, string path, int pathFieldLength)
    {
        var request = new byte[PacketSize];
        BitConverter.GetBytes(opcode).CopyTo(request, 0);
        var ascii = Encoding.ASCII.GetBytes(path.Replace('/', '\\'));
        var count = Math.Min(ascii.Length, pathFieldLength - 1);
        Array.Copy(ascii, 0, request, 4, count);
        request[4 + count] = 0;
        return request;
    }

    private static string ReadAsciiZ(byte[] buffer, int offset, int maxLength)
    {
        var count = 0;
        while (count < maxLength && buffer[offset + count] != 0)
        {
            count++;
        }

        return Encoding.ASCII.GetString(buffer, offset, count);
    }

    private readonly record struct PhotoDirectoryPage(
        IReadOnlyList<PhotoDirectoryEntry> Entries,
        int NextCursor,
        bool ReachedEnd);
}

internal static class LsTransportNativeMethods
{
    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSInitUSBDevices", CallingConvention = CallingConvention.Winapi)]
    internal static extern int InitUsbDevices(byte[] deviceInfo, int mode);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSGetTotalUSBDeviceNumber", CallingConvention = CallingConvention.Winapi)]
    internal static extern int GetTotalUsbDeviceNumber(byte[] deviceInfo);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSFindDevSN", CallingConvention = CallingConvention.Winapi)]
    internal static extern int FindDeviceSerial(byte[] context, int index);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSOpenUSBDevice", CallingConvention = CallingConvention.Winapi)]
    internal static extern int OpenUsbDevice(byte[] deviceInfo, byte[] context, ref int error);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSCloseUSBDevice", CallingConvention = CallingConvention.Winapi)]
    internal static extern int CloseUsbDevice(byte[] context);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSReadFlash", CallingConvention = CallingConvention.Winapi)]
    private static extern int ReadFlashRaw(byte[] context, uint offset, uint len1, IntPtr buffer, uint len2);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSWriteFlash", CallingConvention = CallingConvention.Winapi)]
    private static extern int WriteFlashRaw(byte[] context, uint offset, uint len1, IntPtr buffer, uint len2);

    internal static int WriteFlash(byte[] context, uint offset, byte[] request)
    {
        var handle = GCHandle.Alloc(request, GCHandleType.Pinned);
        try
        {
            return WriteFlashRaw(context, offset, (uint)request.Length, handle.AddrOfPinnedObject(), (uint)request.Length);
        }
        finally
        {
            handle.Free();
        }
    }

    internal static bool TryReadFlash(byte[] context, uint offset, int length, out byte[] reply)
    {
        reply = new byte[length];
        var handle = GCHandle.Alloc(reply, GCHandleType.Pinned);
        try
        {
            return ReadFlashRaw(context, offset, (uint)length, handle.AddrOfPinnedObject(), (uint)length) == 1;
        }
        finally
        {
            handle.Free();
        }
    }

    internal static byte[] BuildCandidate(ushort vid, ushort pid)
    {
        var data = new byte[74];
        data[0] = (byte)(vid & 0xff);
        data[1] = (byte)(vid >> 8);
        data[2] = (byte)(pid & 0xff);
        data[3] = (byte)(pid >> 8);
        data[4] = 0x01;

        byte[] prefix = { 0x56, 0x00, 0x54, 0x00, 0x45, 0x00, 0x43, 0x00, 0x48, 0x00 };
        Buffer.BlockCopy(prefix, 0, data, 6, prefix.Length);

        byte[] tail =
        {
            0x55, 0x00, 0x53, 0x00, 0x42, 0x00, 0x2d, 0x00,
            0x4d, 0x00, 0x53, 0x00, 0x44, 0x00, 0x43, 0x00,
            0x20, 0x00, 0x44, 0x00, 0x49, 0x00, 0x53, 0x00,
            0x4b, 0x00, 0x20, 0x00, 0x41, 0x00
        };
        Buffer.BlockCopy(tail, 0, data, 0x28, tail.Length);
        return data;
    }
}

internal static class NativeMethods
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    internal static extern bool SetDllDirectory(string lpPathName);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSInitUSBDevices", CallingConvention = CallingConvention.StdCall)]
    internal static extern int InitUsbDevices(ref DeviceInitConfig config, IntPtr reserved);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSGetTotalUSBDeviceNumber", CallingConvention = CallingConvention.StdCall)]
    internal static extern int GetTotalUsbDeviceNumber(ref DeviceInitConfig config);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSFindDevSN", CallingConvention = CallingConvention.StdCall, CharSet = CharSet.Unicode)]
    private static extern int FindDeviceSerial(StringBuilder serial, int index);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSOpenUSBDevice", CallingConvention = CallingConvention.StdCall, CharSet = CharSet.Unicode)]
    internal static extern int OpenUsbDevice(uint flags, uint reserved, string serial);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSCloseUSBDevice", CallingConvention = CallingConvention.StdCall, CharSet = CharSet.Unicode)]
    internal static extern int CloseUsbDevice(string serial);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSIsUSBDeviceAlive", CallingConvention = CallingConvention.StdCall, CharSet = CharSet.Unicode)]
    internal static extern int IsUsbDeviceAlive(string serial);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSGetVersion", CallingConvention = CallingConvention.StdCall, CharSet = CharSet.Unicode)]
    private static extern int GetVersionRaw(string serial, byte[] buffer, out uint length);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSGetDeviceVersion", CallingConvention = CallingConvention.StdCall, CharSet = CharSet.Unicode)]
    private static extern int GetDeviceVersionRaw(string serial, out ushort version);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSReadFlash", CallingConvention = CallingConvention.StdCall, CharSet = CharSet.Unicode)]
    internal static extern int ReadFlash(string serial, uint address, uint length, byte[] buffer, int mode);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSWriteFlash", CallingConvention = CallingConvention.StdCall, CharSet = CharSet.Unicode)]
    internal static extern int WriteFlash(string serial, uint address, uint length, byte[] buffer, int mode);

    [DllImport("VTech2010USBDllU.dll", EntryPoint = "DLL_LSGetLibraryVersion", CallingConvention = CallingConvention.StdCall)]
    private static extern IntPtr GetLibraryVersionRaw();

    internal static bool TryFindDeviceSerial(int index, out string serial)
    {
        var buffer = new StringBuilder(256);
        var result = FindDeviceSerial(buffer, index);
        serial = buffer.ToString();
        return result == 1 && !string.IsNullOrWhiteSpace(serial);
    }

    internal static bool TryGetVersion(string serial, out string version)
    {
        var buffer = new byte[256];
        var result = GetVersionRaw(serial, buffer, out var length);
        if (result != 1)
        {
            version = string.Empty;
            return false;
        }

        var count = (int)Math.Min(length, (uint)buffer.Length);
        var zeroIndex = Array.IndexOf(buffer, (byte)0, 0, count);
        if (zeroIndex >= 0)
        {
            count = zeroIndex;
        }

        version = Encoding.ASCII.GetString(buffer, 0, count);
        return true;
    }

    internal static bool TryGetDeviceVersion(string serial, out ushort version)
    {
        return GetDeviceVersionRaw(serial, out version) == 1;
    }

    internal static string? GetLibraryVersion()
    {
        var pointer = GetLibraryVersionRaw();
        return pointer == IntPtr.Zero ? null : Marshal.PtrToStringUni(pointer);
    }
}

internal static class MassStorageNativeMethods
{
    [DllImport("DAVTMassStorageLib.dll", EntryPoint = "GetTotalMS_USBDeviceNumber", CallingConvention = CallingConvention.Cdecl)]
    internal static extern int GetTotalMsUsbDeviceNumber(ref MassStorageId id);

    [DllImport("DAVTMassStorageLib.dll", EntryPoint = "GetMS_USBDeviceVolume", CallingConvention = CallingConvention.Cdecl, CharSet = CharSet.Unicode)]
    internal static extern int GetMsUsbDeviceVolume(ref MassStorageId id, int index, StringBuilder volumePath);

    [DllImport("DAVTMassStorageLib.dll", EntryPoint = "GetUSBDeviceInfoEx", CallingConvention = CallingConvention.Cdecl, CharSet = CharSet.Unicode)]
    private static extern int GetUsbDeviceInfoExRaw(
        string drive,
        out byte deviceType,
        StringBuilder description,
        out byte inquiryType,
        StringBuilder extra);

    internal static bool TryGetUsbDeviceInfoEx(
        string drive,
        out byte deviceType,
        out string description,
        out byte inquiryType,
        out string extra)
    {
        var descriptionBuffer = new StringBuilder(512);
        var extraBuffer = new StringBuilder(512);
        var result = GetUsbDeviceInfoExRaw(drive, out deviceType, descriptionBuffer, out inquiryType, extraBuffer);
        description = descriptionBuffer.ToString();
        extra = extraBuffer.ToString();
        return result == 0;
    }
}

internal static class KernelNativeMethods
{
    internal const uint GenericRead = 0x80000000;
    internal const uint GenericWrite = 0x40000000;
    internal const uint FileShareRead = 0x00000001;
    internal const uint FileShareWrite = 0x00000002;
    internal const uint OpenExisting = 3;
    internal const uint FileBegin = 0;
    private const uint IoctlStorageGetDeviceNumber = 0x002D1080;
    private const uint IoctlDiskGetLengthInfo = 0x0007405C;
    private const uint FsctlLockVolume = 0x00090018;
    private const uint FsctlDismountVolume = 0x00090020;
    private const uint FsctlAllowExtendedDasdIo = 0x00090083;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    internal static extern SafeFileHandle CreateFile(
        string lpFileName,
        uint dwDesiredAccess,
        uint dwShareMode,
        IntPtr lpSecurityAttributes,
        uint dwCreationDisposition,
        uint dwFlagsAndAttributes,
        IntPtr hTemplateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool FlushFileBuffers(SafeFileHandle hFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool SetFilePointerEx(
        SafeFileHandle hFile,
        long liDistanceToMove,
        out long lpNewFilePointer,
        uint dwMoveMethod);

    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool ReadFile(
        SafeFileHandle hFile,
        byte[] lpBuffer,
        int nNumberOfBytesToRead,
        out int lpNumberOfBytesRead,
        IntPtr lpOverlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool WriteFile(
        SafeFileHandle hFile,
        byte[] lpBuffer,
        int nNumberOfBytesToWrite,
        out int lpNumberOfBytesWritten,
        IntPtr lpOverlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool DeviceIoControl(
        SafeFileHandle hDevice,
        uint dwIoControlCode,
        IntPtr lpInBuffer,
        uint nInBufferSize,
        IntPtr lpOutBuffer,
        uint nOutBufferSize,
        out uint lpBytesReturned,
        IntPtr lpOverlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool DeviceIoControl(
        SafeFileHandle hDevice,
        uint dwIoControlCode,
        IntPtr lpInBuffer,
        uint nInBufferSize,
        out StorageDeviceNumber lpOutBuffer,
        uint nOutBufferSize,
        out uint lpBytesReturned,
        IntPtr lpOverlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool DeviceIoControl(
        SafeFileHandle hDevice,
        uint dwIoControlCode,
        IntPtr lpInBuffer,
        uint nInBufferSize,
        out GetLengthInformation lpOutBuffer,
        uint nOutBufferSize,
        out uint lpBytesReturned,
        IntPtr lpOverlapped);

    internal static bool TryGetStorageDeviceNumber(SafeFileHandle handle, out StorageDeviceNumber deviceNumber)
    {
        return DeviceIoControl(
            handle,
            IoctlStorageGetDeviceNumber,
            IntPtr.Zero,
            0,
            out deviceNumber,
            (uint)Marshal.SizeOf<StorageDeviceNumber>(),
            out _,
            IntPtr.Zero);
    }

    internal static bool TryGetLengthInfo(SafeFileHandle handle, out ulong length)
    {
        var ok = DeviceIoControl(
            handle,
            IoctlDiskGetLengthInfo,
            IntPtr.Zero,
            0,
            out GetLengthInformation info,
            (uint)Marshal.SizeOf<GetLengthInformation>(),
            out _,
            IntPtr.Zero);
        length = ok ? (ulong)info.Length : 0;
        return ok;
    }

    internal static void PrepareMountedVolumeForRawIo(SafeFileHandle handle)
    {
        foreach (var code in new[] { FsctlLockVolume, FsctlDismountVolume, FsctlAllowExtendedDasdIo })
        {
            _ = DeviceIoControl(
                handle,
                code,
                IntPtr.Zero,
                0,
                IntPtr.Zero,
                0,
                out _,
                IntPtr.Zero);
        }
    }
}

internal sealed class MailboxVolume : IDisposable
{
    private const int SectorSize = 512;
    private const int PartitionStartLba = 8;
    private const int AbsoluteReplyLba = 15280;
    private const int AbsoluteRequestLba = 15536;
    private const int AbsoluteCtrlALba = 15832;
    private const int AbsoluteCtrlBLba = 15834;
    private const uint CtrlMagic = 0x00000006;
    private const uint CtrlActiveBase = 0x00002800;
    private const int PhotoPathFieldBytes = 42;

    private readonly SafeFileHandle _handle;
    private readonly int _replyLba;
    private readonly int _requestLba;
    private readonly int _ctrlALba;
    private readonly int _ctrlBLba;

    private MailboxVolume(SafeFileHandle handle, int replyLba, int requestLba, int ctrlALba, int ctrlBLba)
    {
        _handle = handle;
        _replyLba = replyLba;
        _requestLba = requestLba;
        _ctrlALba = ctrlALba;
        _ctrlBLba = ctrlBLba;
        KernelNativeMethods.PrepareMountedVolumeForRawIo(_handle);
    }

    internal static MailboxVolume Open(string volume)
    {
        var handle = KernelNativeMethods.CreateFile(
            ToMailboxVolumePath(volume),
            KernelNativeMethods.GenericRead | KernelNativeMethods.GenericWrite,
            KernelNativeMethods.FileShareRead | KernelNativeMethods.FileShareWrite,
            IntPtr.Zero,
            KernelNativeMethods.OpenExisting,
            0,
            IntPtr.Zero);
        if (handle.IsInvalid)
        {
            throw new InvalidOperationException($"failed to open {volume} for mailbox I/O");
        }

        return new MailboxVolume(
            handle,
            AbsoluteReplyLba - PartitionStartLba,
            AbsoluteRequestLba - PartitionStartLba,
            AbsoluteCtrlALba - PartitionStartLba,
            AbsoluteCtrlBLba - PartitionStartLba);
    }

    internal static MailboxVolume OpenPhysical(string physicalPath)
    {
        var handle = KernelNativeMethods.CreateFile(
            physicalPath,
            KernelNativeMethods.GenericRead | KernelNativeMethods.GenericWrite,
            KernelNativeMethods.FileShareRead | KernelNativeMethods.FileShareWrite,
            IntPtr.Zero,
            KernelNativeMethods.OpenExisting,
            0,
            IntPtr.Zero);
        if (handle.IsInvalid)
        {
            throw new InvalidOperationException($"failed to open {physicalPath} for mailbox I/O");
        }

        return new MailboxVolume(handle, AbsoluteReplyLba, AbsoluteRequestLba, AbsoluteCtrlALba, AbsoluteCtrlBLba);
    }

    internal int PathType(string path)
    {
        var reply = SingleCommand(PackPathCommand(0x10, path));
        var rawKind = BitConverter.ToUInt32(reply, 0);
        return (int)(rawKind & 0xFFFF);
    }

    internal int StatFileSize(string path)
    {
        var reply = SingleCommand(PackPathCommand(0x09, path));
        return (int)BitConverter.ToUInt32(reply, 4);
    }

    internal byte[] SendCommand(byte[] request, int replySectorCount = 1)
    {
        if (replySectorCount <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(replySectorCount));
        }

        var requestSectorCount = Math.Max(1, request.Length / SectorSize);
        RingB(requestSectorCount);
        Thread.Sleep(2);
        WriteSectors(_requestLba, request);
        Thread.Sleep(2);
        RingA(replySectorCount);
        Thread.Sleep(5);
        var reply = ReadSectors(_replyLba, replySectorCount);
        if (RuntimeState.TraceProtocol)
        {
            var opcode = BitConverter.ToUInt32(request, 0);
            Console.Error.WriteLine($"mailbox op=0x{opcode:x2} req={HexPreviewLocal(request, 32)}");
            Console.Error.WriteLine($"mailbox op=0x{opcode:x2} rsp={HexPreviewLocal(reply, Math.Min(64, reply.Length))}");
        }

        return reply;
    }

    public void Dispose()
    {
        if (!_handle.IsClosed)
        {
            _handle.Dispose();
        }
    }

    private byte[] SingleCommand(byte[] request)
    {
        return SendCommand(request, 1);
    }

    private void RingA(int sectorCount)
    {
        WriteSectors(_ctrlALba, PackControl(sectorCount));
    }

    private void RingB(int sectorCount)
    {
        WriteSectors(_ctrlBLba, PackControl(sectorCount));
    }

    private static string ToMailboxVolumePath(string volume)
    {
        return $@"\\.\{volume.TrimEnd('\\')}";
    }

    private static byte[] PackControl(int sectorCount)
    {
        if (sectorCount < 0 || sectorCount > byte.MaxValue)
        {
            throw new ArgumentOutOfRangeException(nameof(sectorCount));
        }

        var buffer = new byte[SectorSize];
        BitConverter.GetBytes(CtrlActiveBase).CopyTo(buffer, 0);
        BitConverter.GetBytes(CtrlMagic | ((uint)sectorCount << 24)).CopyTo(buffer, 4);
        return buffer;
    }

    private static byte[] PackPathCommand(uint opcode, string path)
    {
        var buffer = new byte[SectorSize];
        BitConverter.GetBytes(opcode).CopyTo(buffer, 0);

        var ascii = Encoding.ASCII.GetBytes(path.Replace('/', '\\'));
        var count = Math.Min(ascii.Length, PhotoPathFieldBytes - 1);
        Array.Copy(ascii, 0, buffer, 4, count);
        buffer[4 + count] = 0;
        return buffer;
    }

    private byte[] ReadSectors(int lba, int count)
    {
        var buffer = new byte[count * SectorSize];
        Seek((long)lba * SectorSize);
        var totalRead = 0;
        while (totalRead < buffer.Length)
        {
            var chunk = new byte[buffer.Length - totalRead];
            if (!KernelNativeMethods.ReadFile(_handle, chunk, chunk.Length, out var read, IntPtr.Zero) || read <= 0)
            {
                throw new IOException($"short mailbox read at LBA {lba}");
            }

            Array.Copy(chunk, 0, buffer, totalRead, read);
            totalRead += read;
        }

        return buffer;
    }

    private void WriteSectors(int lba, byte[] data)
    {
        if (data.Length % SectorSize != 0)
        {
            throw new ArgumentException("mailbox writes must be 512-byte aligned", nameof(data));
        }

        Seek((long)lba * SectorSize);
        if (!KernelNativeMethods.WriteFile(_handle, data, data.Length, out var written, IntPtr.Zero) || written != data.Length)
        {
            throw new IOException($"short mailbox write at LBA {lba}");
        }

        if (!KernelNativeMethods.FlushFileBuffers(_handle))
        {
            var error = Marshal.GetLastWin32Error();
            if (RuntimeState.TraceProtocol)
            {
                Console.Error.WriteLine($"mailbox flush warning lba={lba} win32={error}");
            }
        }
    }

    private void Seek(long offset)
    {
        if (!KernelNativeMethods.SetFilePointerEx(_handle, offset, out _, KernelNativeMethods.FileBegin))
        {
            throw new IOException($"seek failed for mailbox offset 0x{offset:x}");
        }
    }

    private static string HexPreviewLocal(byte[] data, int maxBytes)
    {
        var count = Math.Min(data.Length, maxBytes);
        var builder = new StringBuilder(count * 3);
        for (var i = 0; i < count; i++)
        {
            if (i > 0)
            {
                builder.Append(' ');
            }

            builder.Append(data[i].ToString("x2"));
        }

        return builder.ToString();
    }
}
