"""Standalone launcher for MaterialClassification_CLI.exe.

Mirrors the pattern of launcher.py (the GUI launcher): finds the project root
(folder containing .venv and cli.py), then runs cli.py inside the bundled
venv with the user's argv passed straight through.

This keeps the CLI exe tiny (~5 MB) instead of bundling torch/transformers
into a multi-GB onefile binary. The .venv must already exist next to it
(Setup.bat creates it on install).
"""
import os
import sys
import subprocess
from pathlib import Path


def _find_project_root() -> Path:
    """Walk upward from the exe/script to find the project root.
    Project root is the directory that contains both 'cli.py' and '.venv'.
    Falls back to the script's parent if the walk fails.
    """
    start = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent

    for candidate in [start, start.parent, start.parent.parent]:
        if (candidate / "cli.py").is_file() and (candidate / ".venv").is_dir():
            return candidate

    return start


def main():
    project_root = _find_project_root()
    python = str(project_root / ".venv" / "Scripts" / "python.exe")
    if not Path(python).exists():
        python = sys.executable  # last resort

    cli_script = str(project_root / "cli.py")

    env = os.environ.copy()
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env.setdefault("HF_HOME", str(project_root / "models" / "hf_cache"))
    env["PYTHONPATH"] = str(project_root)

    cmd = [python, cli_script, *sys.argv[1:]]
    proc = subprocess.run(cmd, cwd=str(project_root), env=env)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
