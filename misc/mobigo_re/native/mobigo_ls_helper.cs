using System;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;

internal static class Program
{
    private const string DllPath = @"C:\Users\Max\Desktop\VTech\DownloadManager\System\VTech2010USBDllU.dll";
    private const uint DefaultFlashSize = 0x04000000;
    private const int ChunkSize = 0x10000;

    [DllImport(DllPath, CallingConvention = CallingConvention.Winapi)]
    private static extern int DLL_LSInitUSBDevices(byte[] deviceInfo, int mode);

    [DllImport(DllPath, CallingConvention = CallingConvention.Winapi)]
    private static extern int DLL_LSGetTotalUSBDeviceNumber(byte[] deviceInfo);

    [DllImport(DllPath, CallingConvention = CallingConvention.Winapi)]
    private static extern int DLL_LSFindDevSN(byte[] ctx, int zero);

    [DllImport(DllPath, CallingConvention = CallingConvention.Winapi)]
    private static extern int DLL_LSOpenUSBDevice(byte[] deviceInfo, byte[] ctx, ref int err);

    [DllImport(DllPath, CallingConvention = CallingConvention.Winapi)]
    private static extern int DLL_LSReadFlash(byte[] ctx, uint offset, uint len1, IntPtr buffer, uint len2);

    [DllImport(DllPath, CallingConvention = CallingConvention.Winapi)]
    private static extern int DLL_LSWriteFlash(byte[] ctx, uint offset, uint len1, IntPtr buffer, uint len2);

    [DllImport(DllPath, CallingConvention = CallingConvention.Winapi)]
    private static extern int DLL_LSCloseUSBDevice(byte[] ctx);

    private static int Main(string[] args)
    {
        if (args.Length == 0)
        {
            Console.Error.WriteLine("usage: mobigo_ls_helper.exe count | dump <output> [size_hex] | read <offset_hex> <length_hex> <output> | write <offset_hex> <input>");
            return 2;
        }

        try
        {
            switch (args[0])
            {
                case "count":
                    return CountDevices();
                case "dump":
                    if (args.Length < 2 || args.Length > 3)
                    {
                        Console.Error.WriteLine("usage: mobigo_ls_helper.exe dump <output> [size_hex]");
                        return 2;
                    }
                    return DumpFlash(args[1], args.Length == 3 ? ParseUInt(args[2]) : DefaultFlashSize);
                case "read":
                    if (args.Length != 4)
                    {
                        Console.Error.WriteLine("usage: mobigo_ls_helper.exe read <offset_hex> <length_hex> <output>");
                        return 2;
                    }
                    return ReadRegion(ParseUInt(args[1]), ParseUInt(args[2]), args[3]);
                case "write":
                    if (args.Length != 3)
                    {
                        Console.Error.WriteLine("usage: mobigo_ls_helper.exe write <offset_hex> <input>");
                        return 2;
                    }
                    return WriteRegion(ParseUInt(args[1]), args[2]);
                default:
                    Console.Error.WriteLine("unknown command");
                    return 2;
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex.Message);
            return 1;
        }
    }

    private static int CountDevices()
    {
        byte[] candidate = BuildCandidate(0x0f88, 0x2d40);
        int rc = DLL_LSInitUSBDevices(candidate, 1);
        if (rc != 1)
        {
            Console.Error.WriteLine("DLL_LSInitUSBDevices failed: " + rc);
            return 1;
        }

        int count = DLL_LSGetTotalUSBDeviceNumber(candidate);
        Console.WriteLine(count);
        return 0;
    }

    private static int DumpFlash(string outputPath, uint size)
    {
        byte[] ctx = OpenDevice();
        byte[] chunk = new byte[ChunkSize];
        GCHandle handle = GCHandle.Alloc(chunk, GCHandleType.Pinned);
        try
        {
            using (FileStream fs = new FileStream(outputPath, FileMode.Create, FileAccess.Write, FileShare.None))
            {
                uint offset = 0;
                while (offset < size)
                {
                    uint todo = Math.Min((uint)ChunkSize, size - offset);
                    Array.Clear(chunk, 0, chunk.Length);
                    int rc = DLL_LSReadFlash(ctx, offset, todo, handle.AddrOfPinnedObject(), todo);
                    if (rc != 1)
                    {
                        throw new InvalidOperationException(string.Format(CultureInfo.InvariantCulture, "DLL_LSReadFlash failed at 0x{0:X8}: {1}", offset, rc));
                    }
                    fs.Write(chunk, 0, (int)todo);
                    offset += todo;
                    if ((offset & 0x000fffff) == 0 || offset == size)
                    {
                        Console.WriteLine(string.Format(CultureInfo.InvariantCulture, "progress 0x{0:X8}/0x{1:X8}", offset, size));
                    }
                }
            }
        }
        finally
        {
            if (handle.IsAllocated)
            {
                handle.Free();
            }
            DLL_LSCloseUSBDevice(ctx);
        }

        Console.WriteLine(outputPath);
        return 0;
    }

    private static int ReadRegion(uint offset, uint length, string outputPath)
    {
        byte[] ctx = OpenDevice();
        byte[] chunk = new byte[length];
        GCHandle handle = GCHandle.Alloc(chunk, GCHandleType.Pinned);
        try
        {
            int rc = DLL_LSReadFlash(ctx, offset, length, handle.AddrOfPinnedObject(), length);
            if (rc != 1)
            {
                throw new InvalidOperationException(string.Format(CultureInfo.InvariantCulture, "DLL_LSReadFlash failed at 0x{0:X8}: {1}", offset, rc));
            }
            File.WriteAllBytes(outputPath, chunk);
        }
        finally
        {
            if (handle.IsAllocated)
            {
                handle.Free();
            }
            DLL_LSCloseUSBDevice(ctx);
        }

        Console.WriteLine(outputPath);
        return 0;
    }

    private static int WriteRegion(uint offset, string inputPath)
    {
        byte[] payload = File.ReadAllBytes(inputPath);
        byte[] ctx = OpenDevice();
        GCHandle handle = GCHandle.Alloc(payload, GCHandleType.Pinned);
        try
        {
            int rc = DLL_LSWriteFlash(ctx, offset, (uint)payload.Length, handle.AddrOfPinnedObject(), (uint)payload.Length);
            if (rc != 1)
            {
                throw new InvalidOperationException(string.Format(CultureInfo.InvariantCulture, "DLL_LSWriteFlash failed at 0x{0:X8}: {1}", offset, rc));
            }
        }
        finally
        {
            if (handle.IsAllocated)
            {
                handle.Free();
            }
            DLL_LSCloseUSBDevice(ctx);
        }

        Console.WriteLine(string.Format(CultureInfo.InvariantCulture, "wrote 0x{0:X} bytes at 0x{1:X8} from {2}", payload.Length, offset, inputPath));
        return 0;
    }

    private static byte[] OpenDevice()
    {
        byte[] candidate = BuildCandidate(0x0f88, 0x2d40);
        int rc = DLL_LSInitUSBDevices(candidate, 1);
        if (rc != 1)
        {
            throw new InvalidOperationException("DLL_LSInitUSBDevices failed: " + rc);
        }

        int count = DLL_LSGetTotalUSBDeviceNumber(candidate);
        if (count < 1)
        {
            throw new InvalidOperationException("No matching MobiGo device found");
        }

        byte[] ctx = new byte[64];
        rc = DLL_LSFindDevSN(ctx, 0);
        if (rc != 1)
        {
            throw new InvalidOperationException("DLL_LSFindDevSN failed: " + rc);
        }

        int err = -1;
        rc = DLL_LSOpenUSBDevice(candidate, ctx, ref err);
        if (rc != 1)
        {
            throw new InvalidOperationException("DLL_LSOpenUSBDevice failed: rc=" + rc + " err=" + err);
        }
        return ctx;
    }

    private static byte[] BuildCandidate(ushort vid, ushort pid)
    {
        byte[] data = new byte[74];
        data[0] = (byte)(vid & 0xff);
        data[1] = (byte)((vid >> 8) & 0xff);
        data[2] = (byte)(pid & 0xff);
        data[3] = (byte)((pid >> 8) & 0xff);
        data[4] = 0x01;

        byte[] prefix = {
            0x56, 0x00, 0x54, 0x00, 0x45, 0x00, 0x43, 0x00, 0x48, 0x00
        };
        Buffer.BlockCopy(prefix, 0, data, 6, prefix.Length);

        byte[] tail = {
            0x55, 0x00, 0x53, 0x00, 0x42, 0x00, 0x2d, 0x00,
            0x4d, 0x00, 0x53, 0x00, 0x44, 0x00, 0x43, 0x00,
            0x20, 0x00, 0x44, 0x00, 0x49, 0x00, 0x53, 0x00,
            0x4b, 0x00, 0x20, 0x00, 0x41, 0x00
        };
        Buffer.BlockCopy(tail, 0, data, 0x28, tail.Length);
        return data;
    }

    private static uint ParseUInt(string text)
    {
        if (text.StartsWith("0x", StringComparison.OrdinalIgnoreCase))
        {
            return uint.Parse(text.Substring(2), NumberStyles.HexNumber, CultureInfo.InvariantCulture);
        }
        return uint.Parse(text, CultureInfo.InvariantCulture);
    }
}
