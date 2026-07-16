# Silent installer - contract validation

This proves `MaterialClassification_Silent_Setup.exe` (built by
`build_silent_installer.ps1`, run by `silent_install.ps1`) satisfies the
universal contract in `MANAGED_TOOL_INSTALLERS.md` section 2, so JARVIS can
manage and auto-update it.

## Mechanism

A small **native** launcher (`native_launcher.c`, compiled in-box with MSVC
`cl.exe`) with the payload appended as an overlay:

```
[ native_launcher.exe ][ payload.zip ][ int64 zipLength ][ magic "MCSFX001" ]
```

JARVIS runs `MaterialClassification_Silent_Setup.exe -InstallDir {INSTALL_DIR}`.
The native stub parses `-InstallDir`, then runs the embedded extraction script
(`silent_extract.ps1`, UTF-16 base64 baked into the exe) via
`powershell -EncodedCommand`, passing paths through environment variables. The
script reads the exe as **data** through a read-only ZIP64 sub-stream (no temp
copy), extracts the payload to a scratch temp dir, runs
`silent_install.ps1 -InstallDir <dir>`, and returns that script's exit code.

Two design decisions, both forced by testing:

- **Native, not a 7-Zip SFX.** The 7-Zip console/GUI SFX modules cannot take a
  redirectable install dir: unknown args make them error out (exit 7) without
  running, and the native `-o` switch extracts but skips `RunProgram`. A stub we
  control owns argument parsing and the exit code end to end.
- **Native, not managed (.NET).** A managed (C#) launcher works for small
  payloads but Windows **cannot LAUNCH a managed exe carrying a >4 GB overlay**
  (the CLR loader chokes: "not a valid application for this OS platform" -
  observed on the real 18 GB build). A native PE ignores the overlay entirely,
  so the 18 GB single-file exe launches fine; the extraction runs in PowerShell,
  which reads the file as data, not through the PE loader.

Clause letters match the contract table.

| Clause | Requirement | How this installer satisfies it |
|---|---|---|
| **(a) Silent** | Fully unattended, no windows/prompts | The native stub launches PowerShell with `CREATE_NO_WINDOW`; extraction + `silent_install.ps1` run `-NonInteractive`. No WinForms, no `Read-Host`, no `pause` in the silent path. |
| **(b) Redirectable** | A flag sets the install dir; the whole tree lands there | `install_args = -InstallDir {INSTALL_DIR}`. The stub parses it and passes it on; the installer puts `.venv` + app + models entirely under it. The payload extracts to a scratch temp dir, never into the install dir. |
| **(d) No admin** | Per-user, no UAC | Plain native console exe, no elevation manifest; `silent_install.ps1` makes no HKLM/Program Files/PATH writes. Installs wherever `-InstallDir` points. |
| **(e) Synchronous + honest exit code** | `0` only on full success, non-zero on any failure; `3010` counts as failure | `silent_install.ps1` `exit 0` only at the end after a smoke check; **every pip failure is fatal** (`Invoke-Native` throws -> `exit 1`), not a warning. The stub `WaitForSingleObject`s and returns the child's exit code (`GetExitCodeProcess`). No reboot is ever required. |
| **(f) Deterministic** | Re-run reproduces a working install | Offline wheels (`--no-index`), pinned payload; a second run into a fresh dir reproduces the same tree. |
| **(g) Re-runnable / idempotent** | Re-running re-establishes this version's state | Required because `self_contained:false`. Re-run reuses an existing `.venv`, pip skips satisfied packages, app code is re-copied with `-Force`, and `app_config.json` / `shapefile_config.json` are **preserved** (the `preserve` list). |

## Why `self_contained: false` + the shared-data split

Per contract section 3, `material_classification` is the activation-profile
example: it carries per-box operator config (`app_config.json` ->
`sam3_local_dir`, `hf_cache_dir`; `shapefile_config.json`). The installer
**never overwrites** those two files on a re-run, and they are declared in
`preserve` so JARVIS keeps the box-local copy across updates.

**The 4 GB exe limit forces the split.** A single `.exe` above 4 GiB cannot be
launched by Windows (verified: 3.9 GB launches, 4.1 GB fails with "not a valid
application for this OS platform"). The full offline payload is ~17 GB, so the
heavy, version-stable data — pip wheels + model weights (HF cache + SAM3) — lives
in a **shared per-box dir** provisioned once (`provision_shared.ps1`), and the
managed installer (~170 MB: bundled Python + app code) builds the venv from the
shared wheels and points `app_config` at the shared weights. This shared mutable
state is exactly what the `false` (activation) profile manages: single active
version, serialized switch, prefetch disabled, per-box config preserved.

## The build-time self-test (already run, PASSED)

`build_silent_installer.ps1` builds a tiny throwaway installer whose inner
script records its `-InstallDir` and `exit 42`, runs it as
`selftest.exe -InstallDir C:\__mc_probe__\v1`, and asserts (1) the exe returns
**42** (exit-code propagation) and (2) the install dir was forwarded. If either
fails the build **aborts**. Run it standalone any time (no download, no admin):

```powershell
powershell -File installer_silent\build_silent_installer.ps1 -SelfTestOnly
```

Verified on this machine: exit code returned exactly (42/9/0), `-InstallDir`
forwarded (both `-InstallDir <dir>` and `-InstallDir=<dir>` forms), missing arg
-> exit 2, and multi-file/nested payload extraction with `PayloadDir` pointing at
the scratch dir.

## Disk note

The installer itself is small (~170 MB) and extracts to `%TEMP%` briefly. The
real disk cost is the **per-version `.venv`** it builds (torch + SAM3 deps,
~6 GB) in the install dir, plus the **one-time shared data** (~15 GB at
`C:\JARVIS\shared\material_classification`). The shared data is provisioned once
and reused by every version, so subsequent versions only add another ~6 GB venv.

## Prerequisites

- **Build machine:** MSVC `cl.exe` (Visual Studio / Build Tools, "Desktop
  development with C++") to compile the native stub, plus PowerShell 5.1. The
  build finds `vcvars64.bat` automatically.
- **Target (managed) box:** PowerShell 5.1 + .NET Framework 4.5+ (for
  `System.IO.Compression`, used by the extraction script) - present by default on
  every Windows 10/11 box. The native stub itself needs nothing extra.

## Clean-box end-to-end test (do this on a fresh VM before the first release)

1. Copy `MaterialClassification_Silent_Setup.exe` to a clean Windows 11 x64 box
   with **no** admin rights on the test account.
2. Run it exactly as JARVIS would:
   ```powershell
   $p = Start-Process .\MaterialClassification_Silent_Setup.exe `
        -ArgumentList '-InstallDir','C:\JARVIS\tools\material_classification\<ver>' `
        -Wait -PassThru
   $p.ExitCode        # must be 0, and NO UAC prompt must have appeared
   ```
3. Confirm the tree landed under the given dir and nothing landed elsewhere:
   `...\<ver>\.venv\Scripts\python.exe`, `...\MaterialClassification_CLI.exe`,
   `...\backend`, `...\models`.
4. Run the managed task's entry point and confirm it works immediately:
   ```powershell
   & 'C:\JARVIS\tools\material_classification\<ver>\MaterialClassification_CLI.exe' --examples
   & 'C:\JARVIS\tools\material_classification\<ver>\MaterialClassification_CLI.exe' `
        --manifest examples\sample_data\geocell.toml
   ```
5. **Failure path** (proves clause (e)): temporarily remove a required wheel from
   the payload, rebuild, install -> the exe must exit **non-zero**.
6. **Idempotency** (clause (g)): run a second time into the same dir; it must
   exit `0` and must **not** change `app_config.json` / `shapefile_config.json`
   if you edited them between runs.
7. Measure the real footprint and re-release the recipe with the true value:
   ```powershell
   (Get-ChildItem C:\JARVIS\tools\material_classification\<ver> -Recurse -File |
       Measure-Object -Sum Length).Sum
   ```
