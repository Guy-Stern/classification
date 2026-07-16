<#
    silent_extract.ps1 - extraction stage of the silent installer.

    Run by the NATIVE launcher (native_launcher.c) via `powershell -EncodedCommand`
    (this script's UTF-16 base64 is embedded in the launcher). The native stub
    passes the installer's own path + the target dir via environment variables:
        MC_SELF       full path to MaterialClassification_Silent_Setup.exe
        MC_INSTALLDIR the -InstallDir value JARVIS passed
        MC_GPU        "1" to forward -Gpu, else "0"

    It reads the exe as DATA (a FileStream over a bounded sub-stream - NOT the PE
    loader, so the 18 GB file is fine), extracts the appended ZIP64 overlay to a
    scratch temp dir, runs silent_install.ps1 there, and exits with that script's
    exit code. A native stub is used because Windows cannot LAUNCH a managed
    (.NET) exe with a >4 GB overlay, whereas a native PE ignores the overlay.

    Overlay format (appended to the native launcher):
        [ launcher.exe ][ payload.zip ][ int64 zipLength ][ magic "MCSFX001" ]
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$self = $env:MC_SELF
$dst = $env:MC_INSTALLDIR
$gpu = $env:MC_GPU
if (-not $self -or -not $dst) { [Console]::Error.WriteLine('MC_SELF/MC_INSTALLDIR not set'); exit 3 }

Add-Type -TypeDefinition @"
using System;
using System.IO;
public sealed class McSubStream : Stream {
    private readonly Stream _b; private readonly long _o; private readonly long _l; private long _p;
    public McSubStream(Stream b, long o, long l){ _b=b; _o=o; _l=l; _p=0; _b.Seek(o, SeekOrigin.Begin); }
    public override bool CanRead {get{return true;}}
    public override bool CanSeek {get{return true;}}
    public override bool CanWrite {get{return false;}}
    public override long Length {get{return _l;}}
    public override long Position {get{return _p;} set{ Seek(value, SeekOrigin.Begin);} }
    public override int Read(byte[] buf,int off,int cnt){ if(_p>=_l)return 0; long r=_l-_p; if(cnt>r)cnt=(int)r; _b.Seek(_o+_p,SeekOrigin.Begin); int n=_b.Read(buf,off,cnt); _p+=n; return n; }
    public override long Seek(long off, SeekOrigin origin){ long t; if(origin==SeekOrigin.Begin)t=off; else if(origin==SeekOrigin.Current)t=_p+off; else t=_l+off; if(t<0)t=0; if(t>_l)t=_l; _p=t; return _p; }
    public override void Flush(){}
    public override void SetLength(long v){ throw new NotSupportedException(); }
    public override void Write(byte[] b,int o,int c){ throw new NotSupportedException(); }
}
"@ | Out-Null

Add-Type -AssemblyName System.IO.Compression | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null

$fs = [System.IO.File]::Open($self, 'Open', 'Read', 'Read')
$scratch = Join-Path $env:TEMP ('mcsilent_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch -Force | Out-Null
try {
    $fs.Seek(-16, 'End') | Out-Null
    $tr = New-Object byte[] 16
    $o = 0; while ($o -lt 16) { $n = $fs.Read($tr, $o, 16 - $o); if ($n -le 0) { throw 'short trailer' }; $o += $n }
    if ([System.Text.Encoding]::ASCII.GetString($tr, 8, 8) -ne 'MCSFX001') { throw 'bad trailer magic (corrupt installer?)' }
    $ziplen = [System.BitConverter]::ToInt64($tr, 0)
    $zipstart = $fs.Length - 16 - $ziplen
    if ($zipstart -lt 0) { throw 'bad zip length in trailer' }

    $sub = New-Object McSubStream($fs, $zipstart, $ziplen)
    $zip = New-Object System.IO.Compression.ZipArchive($sub, [System.IO.Compression.ZipArchiveMode]::Read)
    $root = [System.IO.Path]::GetFullPath($scratch) + '\'
    foreach ($e in $zip.Entries) {
        $out = [System.IO.Path]::GetFullPath((Join-Path $scratch $e.FullName))
        if (-not $out.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) { throw "zip entry escapes destination: $($e.FullName)" }
        if ($e.FullName.EndsWith('/') -or $e.FullName.EndsWith('\')) {
            New-Item -ItemType Directory -Path $out -Force | Out-Null; continue
        }
        New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($out)) -Force | Out-Null
        [System.IO.Compression.ZipFileExtensions]::ExtractToFile($e, $out, $true)
    }
    $zip.Dispose()

    $ps1 = Join-Path $scratch 'silent_install.ps1'
    if (-not (Test-Path -LiteralPath $ps1)) { throw 'silent_install.ps1 missing from payload' }
    $a = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $ps1,
        '-InstallDir', $dst, '-PayloadDir', $scratch)
    if ($gpu -eq '1') { $a += '-Gpu' }
    $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $a -Wait -PassThru -NoNewWindow
    exit $p.ExitCode
}
finally {
    $fs.Close()
    Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
}
