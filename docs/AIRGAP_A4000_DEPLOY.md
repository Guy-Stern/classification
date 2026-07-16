# Air-gapped A4000 deploy — silent installer + JARVIS connection

End-to-end runbook to deliver MaterialClassification to a standalone air-gapped
A4000 as a JARVIS-managed tool.

## The 4 GB constraint (why this is a two-part delivery)

Windows cannot **launch** an `.exe` larger than 4 GiB (`CreateProcess` limit —
verified: 3.9 GB launches, 4.1 GB fails). The full offline payload is ~17 GB, so
it can't be one self-extracting installer. Instead (the contract's
`self_contained:false` activation profile):

- **Shared data (~15 GB), provisioned once per box:** the pip wheels + model
  weights (HF cache + SAM3). Version-stable — rarely changes.
- **The managed installer (~170 MB):** bundled Python + app code. Builds each
  version's venv from the shared wheels and points the app at the shared weights.
  New versions ship only this small delta.

Everything the shared data needs already exists in a full publish
(`Publishes\InstallerV19`): `offline_installer\offline_packages*`,
`offline_installer\app\models\{hf_cache,sam3}`, and `sam3_runtime\`.

---

## A. Build the lean installer (dev/build box)

Needs MSVC `cl.exe` ("Desktop development with C++") + PowerShell 5.1. No
downloads.

```powershell
# From a complete publish payload (no interactive prepare_offline needed):
powershell -File installer_silent\build_silent_installer.ps1 `
    -OfflineDir Publishes\InstallerV19\offline_installer `
    -AssetsDir  Publishes\InstallerV19 `
    -OutFile    Publishes\SilentInstaller-1.0.0\MaterialClassification_Silent_Setup.exe
#   -> ~170 MB exe (self-test runs first; build aborts if payload >= 3.9 GB)

# Recipe (version from git tag -> VERSION):
powershell -File installer_silent\make_recipe.ps1 `
    -OutFile Publishes\SilentInstaller-1.0.0\recipe.json
```

`recipe.json`:

```json
{
  "version": "1.0.0",
  "artifact_path": "MaterialClassification_Silent_Setup.exe",
  "packaging": "installer",
  "entry": "MaterialClassification_CLI.exe",
  "install_args": "-InstallDir {INSTALL_DIR}",
  "install_footprint_bytes": 7000000000,
  "self_contained": false,
  "preserve": ["app_config.json", "shapefile_config.json"]
}
```

Also build the JARVIS server + agent exes (JARVIS repo):

```powershell
powershell -File packaging\build-agent.ps1  -Clean          # dist\jarvis-agent.exe
powershell -File packaging\package-server.ps1 -Build         # dist\server-package\
uv run python scripts\make_dev_certs.py --hostname JARVIS-A4000 --ip <a4000-lan-ip>
```

**Carry to the A4000 (USB):** `Publishes\SilentInstaller-1.0.0\` (installer +
recipe), the whole `Publishes\InstallerV19\` folder (source of the ~15 GB shared
data), `installer_silent\provision_shared.ps1`, `dist\server-package\`,
`dist\jarvis-agent.exe`, `ca.crt`.

---

## B. Deploy on the air-gapped A4000 / LAN

### B0. Provision the shared data ONCE
```powershell
powershell -File provision_shared.ps1 -Source E:\InstallerV19 -Gpu
#   -> C:\JARVIS\shared\material_classification\{offline_packages*, hf_cache, sam3, sam3_runtime}
```
The installer finds this path by convention from the install dir
(`<jarvis_root>\shared\material_classification`, where `<jarvis_root>` is the
parent of `tools_root`). Provision elsewhere? Set the `MC_SHARED_DIR` env var for
the agent's service account.

### B1. JARVIS server
Copy `server-package\` to the server box (can be the A4000), then
`start-server.bat`. Its cert must SAN the server hostname/IP (step A). See JARVIS
`docs/TEST_ON_LAN.md`.

### B2. Artifact share
Put the installer on a write-restricted SMB share the agent can read, e.g.
`\\JARVIS-A4000\tools\MaterialClassification-1.0.0-installer.exe`. Harden write
access + enable SMB signing.

### B3. Agent (on the A4000)
`jarvis-agent.exe` + `ca.crt` + `config.yaml`:
```yaml
server_url: https://JARVIS-A4000:8443
ca_cert: C:\jarvis\ca.crt
capabilities:
  - material_classification
tools_root: C:\JARVIS\tools          # shared data is at C:\JARVIS\shared\material_classification
max_concurrent_tasks: 1
work_root: C:\jarvis\work
```
Install as a service (`packaging\install-service.ps1`). **Confirm the agent is
`>= 0.2.0`** (the server withholds an `installer` pin from older agents).

### B4. Register + release the tool (once) — the "JARVIS connection"
```powershell
$S = "https://JARVIS-A4000:8443"
$A = "\\JARVIS-A4000\tools\MaterialClassification-1.0.0-installer.exe"
$id = (curl.exe -sk -X POST $S/api/tools -H "Content-Type: application/json" `
  -d "{""name"":""Material Classification"",""installer_path"":""$A""}" | ConvertFrom-Json).data.id
curl.exe -sk -X PATCH $S/api/tools/$id -H "Content-Type: application/json" `
  -d "{""tool_key"":""material_classification""}"
$body = @{ version="1.0.0"; artifact_path=$A; packaging="installer";
  entry="MaterialClassification_CLI.exe"; install_args="-InstallDir {INSTALL_DIR}";
  install_footprint_bytes=7000000000; self_contained=$false;
  preserve=@("app_config.json","shapefile_config.json") } | ConvertTo-Json
curl.exe -sk -X POST $S/api/tools/$id/versions -H "Content-Type: application/json" -d $body
# then flip the release pointer (Workshop "Release", or PATCH latest_version_id).
```
`tool: material_classification` is already in JARVIS
`task_types/material_classification.yaml` (branch `feat/material-classification-managed`).

### B5. Run it
Submit a `material_classification` task. The agent downloads + sha-verifies the
~170 MB installer, runs `"<installer>" -InstallDir C:\JARVIS\tools\material_classification\1.0.0`,
which builds the venv from the shared wheels, points app_config at the shared
weights, resolves `entry`, and runs `{TOOL_PATH} --manifest <geocell.toml>`.

### B6. Re-measure + re-release the footprint (after the first real install)
```powershell
(Get-ChildItem C:\JARVIS\tools\material_classification\1.0.0 -Recurse -File | Measure-Object -Sum Length).Sum
```
Re-release the recipe with the true `install_footprint_bytes` (the versioned tree
= .venv + code; the shared ~15 GB is separate).

---

## Try the installer directly (no JARVIS)
```powershell
# after B0 provisioning:
MaterialClassification_Silent_Setup.exe -InstallDir C:\JARVIS\tools\material_classification\1.0.0 -Gpu
echo Exit: %ERRORLEVEL%
C:\JARVIS\tools\material_classification\1.0.0\MaterialClassification_CLI.exe --examples
```

## Verify the mechanism anytime
```powershell
powershell -File installer_silent\build_silent_installer.ps1 -SelfTestOnly    # launcher self-test
# full contract vs the real JARVIS agent/server (run with the JARVIS venv):
#   .venv\Scripts\python.exe ..\classification\installer_silent\verify_against_jarvis.py
```
