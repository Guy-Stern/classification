r"""Real end-to-end verification of the MaterialClassification silent installer
against the actual JARVIS agent + server code.

It drives the REAL agent functions (run_installer, _installer_command,
_resolve_exe, _apply_preserve) and the REAL server recipe validation against the
REAL compiled launcher and silent_install.ps1 -- no mocks. Covers contract
clauses (a) silent, (b) redirectable, (e) honest exit code, (g) idempotent +
preserve, plus recipe validation and a live run of the installer brain.

Run it with the JARVIS venv (Python 3.14) so the agent/server imports resolve:

    cd C:\Users\User\Desktop\GSW\JARVIS-managed-tool
    .venv\Scripts\python.exe C:\Users\User\Desktop\GSW\classification\installer_silent\verify_against_jarvis.py

Prerequisites:
  * build_silent_installer.ps1 -SelfTestOnly has been run once (compiles
    _build_tools\SilentSetupLauncher.exe).
  * Set JARVIS_ROOT / PY311_DIR env vars if these repos live elsewhere.
"""
import io
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

CLASS = Path(__file__).resolve().parent.parent
JARVIS = Path(os.environ.get("JARVIS_ROOT", r"C:\Users\User\Desktop\GSW\JARVIS-managed-tool"))
PY311 = Path(os.environ.get("PY311_DIR", r"C:\Users\User\AppData\Local\Programs\Python\Python311"))
sys.path.insert(0, str(JARVIS))

from shared.agent_api import ToolRequirement                       # noqa: E402
from agent.installer_runner import run_installer, InstallerError   # noqa: E402
from agent import tool_manager as TM                               # noqa: E402
from server.routers.tools import VersionCreate, _validate_recipe, _resolve_packaging  # noqa: E402

LAUNCHER = CLASS / "_build_tools" / "SilentSetupLauncher.exe"
REAL_PS1 = CLASS / "installer_silent" / "silent_install.ps1"
MAGIC = b"MCSFX001"
INSTALLER_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

_fails = []
def check(name, cond, detail=""):
    ok = bool(cond)
    print(("  PASS " if ok else "  FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        _fails.append(name)
    return ok

def build_installer_exe(inner_ps1_text, out_exe):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("silent_install.ps1", inner_ps1_text)
    payload = buf.getvalue()
    with open(out_exe, "wb") as f:
        f.write(LAUNCHER.read_bytes())
        f.write(payload)
        f.write(struct.pack("<q", len(payload)))
        f.write(MAGIC)

def make_req(**kw):
    base = dict(
        tool_key="material_classification", version="1.0.0", sha256="0" * 64,
        packaging="installer", size_bytes=123, version_id="v1",
        entry="MaterialClassification_CLI.exe",
        install_args="-InstallDir {INSTALL_DIR}",
        install_footprint_bytes=20_000_000_000,
        self_contained=False, preserve=["app_config.json", "shapefile_config.json"],
    )
    base.update(kw)
    return ToolRequirement(**base)

SUCCESS_INNER = r"""
param([string]$InstallDir,[string]$PayloadDir,[switch]$Gpu)
New-Item -ItemType Directory -Force (Join-Path $InstallDir '.venv\Scripts') | Out-Null
Set-Content -LiteralPath (Join-Path $InstallDir '.venv\Scripts\python.exe') 'stub'
Set-Content -LiteralPath (Join-Path $InstallDir 'MaterialClassification_CLI.exe') 'stub'
if (-not (Test-Path (Join-Path $InstallDir 'app_config.json'))) {
    Set-Content -LiteralPath (Join-Path $InstallDir 'app_config.json') '{"sam3_local_dir":null,"hf_cache_dir":null,"offline_mode":true}'
}
if (-not (Test-Path (Join-Path $InstallDir 'shapefile_config.json'))) {
    Set-Content -LiteralPath (Join-Path $InstallDir 'shapefile_config.json') '{"buildings":[],"roads":[],"water":[]}'
}
exit 0
"""
FAIL_INNER = "param([string]$InstallDir,[string]$PayloadDir,[switch]$Gpu)\nexit 1\n"


def main():
    if not LAUNCHER.exists():
        print(f"ERROR: launcher not built: {LAUNCHER}\n"
              f"Run: powershell -File installer_silent\\build_silent_installer.ps1 -SelfTestOnly")
        sys.exit(2)

    base = Path(os.environ.get("TEMP", r"C:\Temp")) / "mc_verify"
    if " " in str(base):
        base = Path(r"C:\mc_verify")
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)

    print("\n== Test 1: recipe passes the REAL server-side validation ==")
    recipe = dict(
        version="1.0.0", artifact_path="MaterialClassification_Silent_Setup.exe",
        packaging="installer", entry="MaterialClassification_CLI.exe",
        install_args="-InstallDir {INSTALL_DIR}", install_footprint_bytes=20_000_000_000,
        self_contained=False, preserve=["app_config.json", "shapefile_config.json"],
    )
    pkg = _resolve_packaging(recipe["packaging"], recipe["artifact_path"])
    check("packaging resolves to 'installer'", pkg == "installer", pkg)
    try:
        preserved = _validate_recipe(VersionCreate(**recipe), pkg)
        check("recipe accepted by _validate_recipe", True, f"preserve={preserved}")
    except Exception as e:  # noqa: BLE001
        check("recipe accepted by _validate_recipe", False, repr(e))

    def rejected(mut):
        try:
            _validate_recipe(VersionCreate(**{**recipe, **mut}), "installer"); return False
        except Exception:
            return True
    check("rejects install_args without {INSTALL_DIR}", rejected({"install_args": "/silent"}))
    check("rejects entry with '..'", rejected({"entry": "..\\evil.exe"}))
    check("rejects preserve when self_contained=true", rejected({"self_contained": True}))

    print("\n== Test 2: SUCCESS install through the REAL agent code ==")
    version_dir = base / "tools" / "material_classification" / "1.0.0"
    tool_root = version_dir.parent
    version_dir.mkdir(parents=True)
    installer = base / "1.0.0-installer.exe"
    build_installer_exe(SUCCESS_INNER, installer)
    req = make_req()
    command = TM._installer_command(installer, version_dir, req)
    check("command rendered with -InstallDir <version_dir>", "-InstallDir " + str(version_dir) in command)
    try:
        run_installer(command, version_dir, 120, INSTALLER_ENV)
        check("run_installer returned success (exit 0)", True)
    except InstallerError as e:
        check("run_installer returned success (exit 0)", False, str(e))
    check("entry exe laid down", (version_dir / "MaterialClassification_CLI.exe").is_file())
    try:
        exe = TM._resolve_exe(version_dir, req)
        check("agent _resolve_exe found the entry", exe.name == "MaterialClassification_CLI.exe")
    except Exception as e:  # noqa: BLE001
        check("agent _resolve_exe found the entry", False, repr(e))
    TM._apply_preserve(tool_root, version_dir, req)
    check("preserve seeded app_config.json", (tool_root / ".config" / "app_config.json").is_file())
    check("preserve seeded shapefile_config.json", (tool_root / ".config" / "shapefile_config.json").is_file())

    print("\n== Test 3: FAILURE install is detected by the REAL agent ==")
    vdir3 = base / "tools" / "material_classification" / "2.0.0"
    vdir3.mkdir(parents=True)
    inst3 = base / "2.0.0-installer.exe"
    build_installer_exe(FAIL_INNER, inst3)
    raised = False
    try:
        run_installer(TM._installer_command(inst3, vdir3, make_req(version="2.0.0")), vdir3, 60, INSTALLER_ENV)
    except InstallerError:
        raised = True
    check("run_installer raises InstallerError on inner exit 1", raised)

    print("\n== Test 4: idempotent re-run PRESERVES operator-tuned config ==")
    (tool_root / ".config" / "app_config.json").write_text(
        '{"sam3_local_dir":"D:/operator/tuned","hf_cache_dir":null,"offline_mode":true}', encoding="utf-8")
    shutil.rmtree(version_dir); version_dir.mkdir(parents=True)
    run_installer(TM._installer_command(installer, version_dir, req), version_dir, 120, INSTALLER_ENV)
    TM._apply_preserve(tool_root, version_dir, req)
    after = (version_dir / "app_config.json").read_text(encoding="utf-8")
    check("operator's tuned value survives the update", "D:/operator/tuned" in after)

    print("\n== Test 5: the REAL silent_install.ps1 brain (venv + sitecustomize + fatal pip) ==")
    if not PY311.joinpath("python.exe").exists():
        print(f"  SKIP  no full Python 3.11 at {PY311}")
    else:
        payload = base / "realpayload"
        (payload / "app").mkdir(parents=True)
        (payload / "offline_packages").mkdir()
        (payload / "assets").mkdir()
        (payload / "app" / "requirements.txt").write_text("numpy\n", encoding="utf-8")
        (payload / "prerequisites").mkdir()
        link = payload / "prerequisites" / "python311"
        j = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(PY311)], capture_output=True, text=True)
        if j.returncode != 0:
            print("  SKIP  could not create junction:", j.stderr.strip())
        else:
            try:
                idir = base / "realinstall"; idir.mkdir()
                r = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", str(REAL_PS1), "-InstallDir", str(idir), "-PayloadDir", str(payload)],
                    capture_output=True, text=True, timeout=300)
                venv_py = idir / ".venv" / "Scripts" / "python.exe"
                sc = idir / ".venv" / "Lib" / "site-packages" / "sitecustomize.py"
                check("real ps1 created a working .venv", venv_py.is_file())
                check("real ps1 wrote the OpenMP-guard sitecustomize.py",
                      sc.is_file() and "KMP_DUPLICATE_LIB_OK" in sc.read_text(encoding="utf-8", errors="ignore"))
                check("real ps1 exits NON-ZERO when a required wheel is missing (clause e)",
                      r.returncode != 0, f"exit={r.returncode}")
            finally:
                # Remove the junction with rmdir (link only, never the target) BEFORE rmtree.
                subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)

    shutil.rmtree(base, ignore_errors=True)
    print("\n" + ("=" * 50))
    if _fails:
        print(f"RESULT: {len(_fails)} FAILED -> {_fails}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
