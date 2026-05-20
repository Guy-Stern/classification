# -*- mode: python ; coding: utf-8 -*-
#
# Tiny launcher exe — wraps cli_launcher.py, which subprocesses
# cli.py via the bundled .venv. Keeps the exe small (~5 MB) and
# avoids duplicating torch/transformers/etc. inside the binary.
# The CLI's heavy deps live in .venv next to it.

a = Analysis(
    ['cli_launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MaterialClassification_CLI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
