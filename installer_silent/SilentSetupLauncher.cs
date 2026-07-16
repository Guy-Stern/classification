// SilentSetupLauncher.cs
//
// The single self-extracting exe for the JARVIS-managed silent installer.
// Built by build_silent_installer.ps1 which appends a ZIP payload + a 16-byte
// trailer to the compiled launcher:
//
//     [ launcher.exe ][ payload.zip ][ int64 zipLength ][ 8-byte magic ]
//
// At run time the launcher reads the trailer from the end of its OWN file,
// maps the ZIP region with a read-only sub-stream (no temp copy, ZIP64-capable
// so the payload can exceed 4 GB), extracts it to a scratch temp dir, and runs
// silent_install.ps1 -InstallDir <dir>, returning that script's exit code.
//
// Why a custom launcher and not a 7-Zip SFX: the 7-Zip console/GUI SFX modules
// do not forward a redirectable install-dir argument to the inner program
// (unknown args make them error out; -o extracts but skips RunProgram). This
// launcher owns argument parsing and the exit code end-to-end, satisfying
// contract clauses (a) silent, (b) redirectable, (e) honest exit code.
//
// No admin: plain per-user console exe, no elevation manifest.

using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Text;

internal static class SilentSetupLauncher
{
    // Must match TRAILER_MAGIC in build_silent_installer.ps1 (8 bytes).
    private static readonly byte[] Magic = Encoding.ASCII.GetBytes("MCSFX001");

    private static int Main(string[] rawArgs)
    {
        try
        {
            string installDir = null;
            bool gpu = false;
            for (int i = 0; i < rawArgs.Length; i++)
            {
                string a = rawArgs[i];
                if (a.Equals("-InstallDir", StringComparison.OrdinalIgnoreCase) && i + 1 < rawArgs.Length)
                    installDir = rawArgs[++i];
                else if (a.StartsWith("-InstallDir=", StringComparison.OrdinalIgnoreCase))
                    installDir = a.Substring("-InstallDir=".Length);
                else if (a.Equals("-Gpu", StringComparison.OrdinalIgnoreCase))
                    gpu = true;
            }

            if (string.IsNullOrWhiteSpace(installDir))
            {
                Console.Error.WriteLine("ERROR: -InstallDir <dir> is required.");
                return 2;
            }
            installDir = Path.GetFullPath(installDir);

            string exePath = Process.GetCurrentProcess().MainModule.FileName;
            string scratch = Path.Combine(Path.GetTempPath(),
                "mcsilent_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(scratch);

            try
            {
                ExtractPayload(exePath, scratch);

                string ps1 = Path.Combine(scratch, "silent_install.ps1");
                if (!File.Exists(ps1))
                {
                    Console.Error.WriteLine("ERROR: silent_install.ps1 missing from payload.");
                    return 3;
                }

                var psi = new ProcessStartInfo
                {
                    FileName = "powershell.exe",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WorkingDirectory = scratch
                };
                psi.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \""
                    + ps1 + "\" -InstallDir \"" + installDir + "\" -PayloadDir \"" + scratch + "\""
                    + (gpu ? " -Gpu" : "");

                using (var proc = Process.Start(psi))
                {
                    proc.WaitForExit();
                    return proc.ExitCode;   // honest exit code — clause (e)
                }
            }
            finally
            {
                TryDelete(scratch);
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("FATAL: " + ex.Message);
            return 1;
        }
    }

    private static void ExtractPayload(string exePath, string destDir)
    {
        using (var fs = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            long fileLen = fs.Length;
            if (fileLen < 16) throw new InvalidDataException("File too small to contain a payload.");

            fs.Seek(-16, SeekOrigin.End);
            byte[] trailer = ReadExactly(fs, 16);
            for (int i = 0; i < 8; i++)
                if (trailer[8 + i] != Magic[i])
                    throw new InvalidDataException("Payload trailer magic not found (corrupt installer?).");

            long zipLen = BitConverter.ToInt64(trailer, 0);
            long zipStart = fileLen - 16 - zipLen;
            if (zipStart < 0) throw new InvalidDataException("Bad payload length in trailer.");

            using (var sub = new SubStream(fs, zipStart, zipLen))
            using (var zip = new ZipArchive(sub, ZipArchiveMode.Read))
            {
                string root = Path.GetFullPath(destDir) + Path.DirectorySeparatorChar;
                foreach (ZipArchiveEntry entry in zip.Entries)
                {
                    string outPath = Path.GetFullPath(Path.Combine(destDir, entry.FullName));
                    if (!outPath.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                        throw new IOException("Zip entry escapes destination: " + entry.FullName);

                    if (entry.FullName.EndsWith("/") || entry.FullName.EndsWith("\\"))
                    {
                        Directory.CreateDirectory(outPath);
                        continue;
                    }
                    Directory.CreateDirectory(Path.GetDirectoryName(outPath));
                    entry.ExtractToFile(outPath, true);
                }
            }
        }
    }

    private static byte[] ReadExactly(Stream s, int count)
    {
        byte[] buf = new byte[count];
        int off = 0;
        while (off < count)
        {
            int n = s.Read(buf, off, count - off);
            if (n <= 0) throw new EndOfStreamException();
            off += n;
        }
        return buf;
    }

    private static void TryDelete(string dir)
    {
        try { if (Directory.Exists(dir)) Directory.Delete(dir, true); } catch { }
    }
}

// Read-only, seekable view over a [offset, offset+length) slice of a base
// stream. Lets ZipArchive read the appended payload directly (it seeks to the
// slice end for the ZIP central directory) without copying it out first.
internal sealed class SubStream : Stream
{
    private readonly Stream _base;
    private readonly long _offset;
    private readonly long _length;
    private long _pos;

    public SubStream(Stream baseStream, long offset, long length)
    {
        _base = baseStream;
        _offset = offset;
        _length = length;
        _pos = 0;
        _base.Seek(offset, SeekOrigin.Begin);
    }

    public override bool CanRead { get { return true; } }
    public override bool CanSeek { get { return true; } }
    public override bool CanWrite { get { return false; } }
    public override long Length { get { return _length; } }

    public override long Position
    {
        get { return _pos; }
        set { Seek(value, SeekOrigin.Begin); }
    }

    public override int Read(byte[] buffer, int offset, int count)
    {
        if (_pos >= _length) return 0;
        long remaining = _length - _pos;
        if (count > remaining) count = (int)remaining;
        _base.Seek(_offset + _pos, SeekOrigin.Begin);
        int n = _base.Read(buffer, offset, count);
        _pos += n;
        return n;
    }

    public override long Seek(long offset, SeekOrigin origin)
    {
        long target;
        switch (origin)
        {
            case SeekOrigin.Begin: target = offset; break;
            case SeekOrigin.Current: target = _pos + offset; break;
            case SeekOrigin.End: target = _length + offset; break;
            default: throw new ArgumentException("origin");
        }
        if (target < 0) target = 0;
        if (target > _length) target = _length;
        _pos = target;
        return _pos;
    }

    public override void Flush() { }
    public override void SetLength(long value) { throw new NotSupportedException(); }
    public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
}
