# installer_silent/ — JARVIS-managed silent installer

A **separate**, headless installer for the JARVIS Workshop, alongside (and not
replacing) the interactive `ClassificationInstaller.exe` / `Setup.ps1`. It meets
the silent-installer contract in `MANAGED_TOOL_INSTALLERS.md`, so JARVIS installs
it, runs classification tasks against it, and pushes every new version to the
fleet automatically.

## Files

| File | What it is |
|---|---|
| `silent_install.ps1` | The headless installer brain. Does everything `Setup.ps1` + `Post-Install.bat` do, unattended, with fatal exit codes. Creates a real `.venv`, pip-installs the offline wheels (core + torch + `sam3`/`timm`/`triton-windows`), drops the SAM3 BPE asset, copies the program tree, writes `start.bat`, preserves per-box config. Guards the OpenMP exit-139 segfault via a venv `sitecustomize.py`. |
| `SilentSetupLauncher.cs` | The single-exe launcher (compiled in-box). Parses `-InstallDir`, extracts the appended ZIP payload, runs `silent_install.ps1`, and returns its exit code. Chosen over a 7-Zip SFX because the SFX can't take a redirectable install dir. |
| `build_silent_installer.ps1` | Compiles the launcher, ZIPs the offline payload, and appends it as an overlay to produce one `MaterialClassification_Silent_Setup.exe`. Runs a **self-test** first that proves `-InstallDir` is forwarded and the exit code propagates. |
| `make_recipe.ps1` | Emits `recipe.json` (the Workshop release recipe) with the resolved version, `entry`, `install_args`, measured footprint, `self_contained:false`, and the `preserve` list. |
| `VALIDATION.md` | Clause-by-clause proof + the clean-box test procedure. |

## How it's wired into publishing ("every version")

`publish.ps1` (root, `publish.bat` launcher) is the one command that publishes a
version. It:

1. resolves the version — **git tag on HEAD → `git describe` → repo-root `VERSION`**;
2. runs `build_exe.bat` + `prepare_offline.bat` (unchanged);
3. stamps the real version into `version.txt`;
4. builds `MaterialClassification_Silent_Setup.exe` (with the self-test gate);
5. emits `recipe.json`;
6. assembles `Publishes\<version>\` containing the interactive bundle **plus**
   the silent exe and `recipe.json`.

```powershell
.\publish.bat                                  # full publish
.\publish.bat -SkipExeBuild -SkipPrepareOffline  # re-wrap an existing offline tree
```

## The recipe

`entry` is `MaterialClassification_CLI.exe` (the CLI the managed task invokes as
`{TOOL_PATH}`). `install_args` is `-InstallDir {INSTALL_DIR}` — JARVIS substitutes
`C:\JARVIS\tools\material_classification\<version>` for the token and runs the exe
with it. `self_contained` is `false` with `preserve:["app_config.json",
"shapefile_config.json"]` (see `VALIDATION.md` for why).

## Releasing (operator, per version)

1. Bump the version — tag the release (`git tag v1.2.0`) or edit `VERSION`.
2. `.\publish.bat` on the build machine (it has network for the one-time 7-Zip
   bootstrap and Python/wheel staging).
3. Upload `Publishes\<version>\MaterialClassification_Silent_Setup.exe` to the
   write-restricted artifact share.
4. Release the version in the Workshop (PUBLISHING.md Step 4b) using
   `recipe.json`, setting `artifact_path` to the share path.
5. First release only: confirm the whole fleet is on agent `>= 0.2.0`, and that
   `tools_root` is space-free (see contract §7).

## Prerequisites

- **Build machine:** a completed `offline_installer\` tree (from
  `prepare_offline.bat`, including a full venv-capable `prerequisites\python311\`),
  PowerShell 5.1, and .NET Framework (the in-box C# compiler). No downloads, no
  admin.
- **Target (managed) box:** .NET Framework 4.5+ (present by default on Windows
  10/11). A run needs ~2x the installed footprint of transient disk (payload in
  `%TEMP%` + the install tree); see `VALIDATION.md`.
