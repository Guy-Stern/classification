# Air-gapped A4000 deploy — silent installer + JARVIS connection

End-to-end runbook to build the MaterialClassification silent installer, carry it to a
standalone air-gapped A4000 box, and have JARVIS install + run it as a managed tool.

Two halves: **A** builds the bundle on the dev/build box (this repo); **B** deploys JARVIS +
the tool on the air-gapped LAN. The silent installer + its mechanism are fully verified
(`installer_silent/VALIDATION.md`, `installer_silent/verify_against_jarvis.py`).

---

## 0. Before you build — obtain these (current gaps)

The offline bundle is only as complete as what's staged on the build box. Check these first:

| Item | Status on this box | Action |
|---|---|---|
| **SAM3 model weights** | **MISSING** — `models/sam3/` has only tokenizer/config, not the `.pt` | Drop the SAM3 weights into `models/sam3/` (and `models/hf_cache/` if applicable). Without them the CLI runs KMeans-only (`sam3=false`); SAM3 road/building extraction won't work. |
| **CUDA torch wheels (~2.5 GB)** | Not staged (`offline_installer/offline_packages_torch` is ~34 MB) | `prepare_offline.bat` downloads them when you pick the CUDA torch option — needs network at build time. |
| **HF cache (~2 GB)** | Present at `%USERPROFILE%\.cache\huggingface\hub` | `prepare_offline.bat` bundles it. |
| **Built exes** | Not built | `build_exe.bat` (run by `publish.bat`) produces `MaterialClassification_CLI.exe` (the recipe's `entry`) + `ClassificationWebApp.exe`. |
| **Full Python 3.11** | Staged fresh by `prepare_offline.bat` (`prerequisites\python311\`, venv-capable) | The silent installer needs this OR a system Python 3.11 on the target. The A4000 is air-gapped, so bundle it (default). |

---

## A. Build the bundle (dev/build box)

Everything is wired into one command; `prepare_offline.bat` is interactive (torch variant, GPU y/N).

```powershell
# 1. Set the version (git tag preferred; else edit the VERSION file).
git tag v1.0.0                     # or: notepad VERSION

# 2. Publish. Answer prepare_offline's prompts: pick the CUDA torch option and GPU = y for the A4000.
.\publish.bat
#   -> Publishes\1.0.0\
#        MaterialClassification_Silent_Setup.exe   <- the managed silent installer (carry this)
#        recipe.json                               <- the release recipe (carry this)
#        offline_installer\ , ClassificationInstaller.exe , Post-Install.bat  <- interactive path (optional)

# 3. Sanity checks (no full install needed):
powershell -File installer_silent\build_silent_installer.ps1 -SelfTestOnly   # launcher self-test: PASS
type Publishes\1.0.0\recipe.json                                             # confirm fields + footprint
```

`recipe.json` (produced by `make_recipe.ps1`) looks like:

```json
{
  "version": "1.0.0",
  "artifact_path": "MaterialClassification_Silent_Setup.exe",
  "packaging": "installer",
  "entry": "MaterialClassification_CLI.exe",
  "install_args": "-InstallDir {INSTALL_DIR}",
  "install_footprint_bytes": 20000000000,
  "self_contained": false,
  "preserve": ["app_config.json", "shapefile_config.json"]
}
```

> **Disk on the target:** the launcher extracts the payload to `%TEMP%` before installing, so a run
> needs ~**2x the installed footprint** transiently (payload in `%TEMP%` + the tree in the install
> dir), on top of JARVIS's keep-2 baseline. Make sure the A4000 has the headroom.

Also build the JARVIS server + agent exes to carry over (from the JARVIS repo):

```powershell
# in JARVIS-managed-tool, on the build box:
powershell -ExecutionPolicy Bypass -File packaging\build-agent.ps1  -Clean   # -> dist\jarvis-agent.exe
powershell -ExecutionPolicy Bypass -File packaging\package-server.ps1 -Build # -> dist\server-package\
uv run python scripts\make_dev_certs.py --hostname JARVIS-A4000 --ip <a4000-lan-ip>   # cert SANs
```

**Carry to the A4000 (USB):** `Publishes\1.0.0\MaterialClassification_Silent_Setup.exe`,
`Publishes\1.0.0\recipe.json`, `dist\server-package\`, `dist\jarvis-agent.exe`, `ca.crt`.

---

## B. Deploy on the air-gapped A4000 / LAN

### B1. JARVIS server
Copy `server-package\` to the server box (can be the A4000 itself for a single-box test), then
`start-server.bat`. Its cert must list the server hostname/IP as a SAN (done in step A). Full
walkthrough: JARVIS `docs/TEST_ON_LAN.md`.

### B2. Artifact share
Put the installer on a write-restricted SMB share the agent can read, e.g.
`\\JARVIS-A4000\tools\MaterialClassification-1.0.0-installer.exe`. Harden write access to the
release identity + enable SMB signing (installer mode runs downloaded code at install time).

### B3. Agent (on the A4000)
Stage `jarvis-agent.exe` + `ca.crt` + `config.yaml`:

```yaml
server_url: https://JARVIS-A4000:8443
ca_cert: C:\jarvis\ca.crt
capabilities:
  - material_classification        # managed: installed from the Workshop on first use
tools_root: C:\JARVIS\tools         # space-free (required); versions land here side-by-side
max_concurrent_tasks: 1
work_root: C:\jarvis\work
```

Install as a service: `packaging\install-service.ps1`. **Confirm the agent is `>= 0.2.0`** — the
server withholds an `installer` pin from an older agent (the task stays queued).

### B4. Register + release the tool (once) — the "JARVIS connection"
From any box that can reach the server (values come straight from `recipe.json`):

```powershell
$S = "https://JARVIS-A4000:8443"
$A = "\\JARVIS-A4000\tools\MaterialClassification-1.0.0-installer.exe"   # the share path

# register + bind the tool_key
$id = (curl.exe -sk -X POST $S/api/tools -H "Content-Type: application/json" `
  -d "{""name"":""Material Classification"",""installer_path"":""$A""}" | ConvertFrom-Json).data.id
curl.exe -sk -X PATCH $S/api/tools/$id -H "Content-Type: application/json" `
  -d "{""tool_key"":""material_classification""}"

# release the version WITH the recipe (activation profile: self_contained=false + preserve)
$body = @{
  version                 = "1.0.0"
  artifact_path           = $A
  packaging               = "installer"
  entry                   = "MaterialClassification_CLI.exe"
  install_args            = "-InstallDir {INSTALL_DIR}"
  install_footprint_bytes = 20000000000          # replace with the measured value after a real install
  self_contained          = $false
  preserve                = @("app_config.json","shapefile_config.json")
} | ConvertTo-Json
curl.exe -sk -X POST $S/api/tools/$id/versions -H "Content-Type: application/json" -d $body
# then flip the release pointer to this version (Workshop "Release", or PATCH latest_version_id).
```

The `tool: material_classification` binding is already in JARVIS
`task_types/material_classification.yaml` (branch `feat/material-classification-managed`).

### B5. Run it
Submit a `material_classification` task (region + output; `source: staged` with an `input_root`, or
`infrao`). The agent:
1. sees the `material_classification` pin, downloads + sha-verifies the installer to a staging dir,
2. runs `"<installer>" -InstallDir C:\JARVIS\tools\material_classification\1.0.0` (silent, no admin),
3. resolves `entry` (`MaterialClassification_CLI.exe`) and seeds the preserved config,
4. runs `{TOOL_PATH} --manifest <geocell.toml>` and streams stdout into the task log.

Watch the agent log / Workshop; the first task installs, later ones reuse the version.

### B6. Re-measure the footprint (after the first real install)
```powershell
(Get-ChildItem C:\JARVIS\tools\material_classification\1.0.0 -Recurse -File | Measure-Object -Sum Length).Sum
```
Re-release the recipe with the true `install_footprint_bytes` so the disk preflight is accurate.

---

## Verify anytime
```powershell
# launcher mechanism (arg-forwarding + exit-code), no download/admin:
powershell -File installer_silent\build_silent_installer.ps1 -SelfTestOnly
# full contract vs. the real JARVIS agent/server code (run with the JARVIS venv):
#   cd ..\JARVIS-managed-tool ; .venv\Scripts\python.exe ..\classification\installer_silent\verify_against_jarvis.py
```
