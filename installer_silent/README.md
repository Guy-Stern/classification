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
| `native_launcher.c` | The single-exe **native** stub (compiled in-box with MSVC `cl.exe`). Parses `-InstallDir`, then runs the embedded `silent_extract.ps1` via `powershell -EncodedCommand`. Native because Windows can't launch a *managed* exe with a >4 GB overlay; a native PE ignores the overlay. |
| `silent_extract.ps1` | Extraction stage (base64-embedded in the stub). Reads the exe as data via a ZIP64 sub-stream, extracts the payload to a scratch temp dir, runs `silent_install.ps1`, returns its exit code. |
| `build_silent_installer.ps1` | Compiles the native stub, ZIPs the offline payload, and appends it as an overlay to produce one `MaterialClassification_Silent_Setup.exe`. Runs a **self-test** first that proves `-InstallDir` is forwarded and the exit code propagates. |
| `provision_shared.ps1` | Stages the ~15 GB **shared data** (wheels + weights) once per box from a full publish (e.g. `Publishes\InstallerV19`). The installer reads wheels/weights from here. |
| `make_recipe.ps1` | Emits `recipe.json` (the Workshop release recipe) with the resolved version, `entry`, `install_args`, measured footprint, `self_contained:false`, and the `preserve` list. |
| `verify_against_jarvis.py` | Real end-to-end checks against the actual JARVIS agent + server code (run with the JARVIS venv). |
| `VALIDATION.md` | Clause-by-clause proof + the clean-box test procedure. |

## The 4 GB limit and the shared-data split

Windows can't launch an `.exe` above 4 GiB, and the full offline payload is
~17 GB. So the heavy, version-stable data (pip wheels + HF cache + SAM3 weights)
is provisioned **once per box** into `C:\JARVIS\shared\material_classification\`
(`provision_shared.ps1`), and the managed installer is a small (~170 MB)
bootstrap that builds each version's venv from those shared wheels and points
`app_config.json` at the shared weights. The installer finds the shared dir by
convention from the install dir (`<jarvis_root>\shared\material_classification`),
or via the `MC_SHARED_DIR` env var. See `AIRGAP_A4000_DEPLOY.md`.

**The artifact is not standalone.** The ~170 MB exe is only bundled Python 3.11
(164 MB) + app code (~14 MB) — no wheels, no weights. Without the shared data it
exits **1** at the pre-flight check in `silent_install.ps1`. Three sizes not to
conflate, whoever is wiring this into JARVIS:

| | Size | Paid |
|---|---|---|
| The artifact JARVIS downloads | ~170 MB | per version, over the wire |
| Shared data (`provision_shared.ps1`) | ~15 GB | **once per box**, out of band |
| Install footprint (`.venv` + code) | ~6.8 GB | **per installed version** |

`AIRGAP_A4000_DEPLOY.md` has the full per-directory accounting.

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
  PowerShell 5.1, and MSVC `cl.exe` ("Desktop development with C++") to compile
  the native stub. No downloads, no admin. (You can also build from a previous
  `Publishes\InstallerV*\offline_installer` payload instead of re-running
  `prepare_offline.bat`.)
- **Target (managed) box:** PowerShell 5.1 + .NET Framework 4.5+ (for the
  extraction script's `System.IO.Compression`) - present by default on Windows
  10/11. A run needs ~2x the installed footprint of transient disk (payload in
  `%TEMP%` + the install tree); see `VALIDATION.md`.
