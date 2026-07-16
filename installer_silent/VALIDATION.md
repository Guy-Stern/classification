# Silent installer - contract validation

This proves `MaterialClassification_Silent_Setup.exe` (built by
`build_silent_installer.ps1`, run by `silent_install.ps1`) satisfies the
universal contract in `MANAGED_TOOL_INSTALLERS.md` section 2, so JARVIS can
manage and auto-update it.

## Mechanism

A small C# launcher (`SilentSetupLauncher.cs`, compiled in-box - no external
SDK, no admin) with the payload appended as an overlay:

```
[ launcher.exe ][ payload.zip ][ int64 zipLength ][ magic "MCSFX001" ]
```

JARVIS runs `MaterialClassification_Silent_Setup.exe -InstallDir {INSTALL_DIR}`.
The launcher parses `-InstallDir` itself, maps the ZIP with a read-only
sub-stream (ZIP64, so the payload can exceed 4 GB - no temp copy), extracts it
to a scratch temp dir, and runs `silent_install.ps1 -InstallDir <dir>`,
returning that script's exit code.

We chose this over a 7-Zip SFX after empirically confirming the 7-Zip
console/GUI SFX modules cannot take a redirectable install dir: unknown args
make them error out (exit 7) without running, and the native `-o` switch
extracts but skips `RunProgram`. The custom launcher owns argument parsing and
the exit code end to end.

Clause letters match the contract table.

| Clause | Requirement | How this installer satisfies it |
|---|---|---|
| **(a) Silent** | Fully unattended, no windows/prompts | The launcher runs with `CreateNoWindow=true` and starts `powershell.exe -NonInteractive -File silent_install.ps1`. No WinForms, no `Read-Host`, no `pause` in the silent path. |
| **(b) Redirectable** | A flag sets the install dir; the whole tree lands there | `install_args = -InstallDir {INSTALL_DIR}`. The launcher parses it and passes it on; the installer puts `.venv` + app + models entirely under it. The payload extracts to a scratch temp dir, never into the install dir. |
| **(d) No admin** | Per-user, no UAC | Plain console exe, no elevation manifest; `silent_install.ps1` makes no HKLM/Program Files/PATH writes. Installs wherever `-InstallDir` points. |
| **(e) Synchronous + honest exit code** | `0` only on full success, non-zero on any failure; `3010` counts as failure | `silent_install.ps1` `exit 0` only at the end after a smoke check; **every pip failure is fatal** (`Invoke-Native` throws -> `exit 1`), not a warning. The launcher `WaitForExit()`s and returns `proc.ExitCode`. No reboot is ever required. |
| **(f) Deterministic** | Re-run reproduces a working install | Offline wheels (`--no-index`), pinned payload; a second run into a fresh dir reproduces the same tree. |
| **(g) Re-runnable / idempotent** | Re-running re-establishes this version's state | Required because `self_contained:false`. Re-run reuses an existing `.venv`, pip skips satisfied packages, app code is re-copied with `-Force`, and `app_config.json` / `shapefile_config.json` are **preserved** (the `preserve` list). |

## Why `self_contained: false`

Per contract section 3, `material_classification` is the activation-profile
example: it carries per-box operator config (`app_config.json` ->
`sam3_local_dir`, `hf_cache_dir`; `shapefile_config.json`). The installer
**never overwrites** those two files on a re-run, and they are declared in
`preserve` so JARVIS keeps the box-local copy across updates. Model weights are
bundled in-folder, so the `false` classification is conservative-safe.

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

The launcher extracts the full payload to `%TEMP%` before installing, so a run
needs roughly **2x the installed footprint transiently** (payload in `%TEMP%` +
the tree in the install dir), on top of the contract's keep-2 baseline. Ensure
the worker has that headroom; the scratch dir is deleted when the launcher exits.

## Target prerequisite

.NET Framework 4.5+ (for `System.IO.Compression` in the launcher). Present by
default on every Windows 10/11 box - not an extra install.

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
