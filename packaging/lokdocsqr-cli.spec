# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the headless LokDocSQR CLI (one-dir build).

Build from the repository root:

    pyinstaller packaging/lokdocsqr-cli.spec --noconfirm

The resulting executable is ``dist/lokdocsqr/lokdocsqr`` (``.exe`` on
Windows). This spec intentionally excludes Qt/PySide: the CLI never imports
``lokdocsqr.gui``, so the desktop stack is not part of this binary.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

# ``nodes/__init__.py`` imports every built-in node package, but the plugin
# discovery path uses ``importlib.import_module("lokdocsqr.nodes")``. Collect
# all submodules explicitly so freezing never drops a node family. The GUI
# package is intentionally left out of the CLI binary.
hiddenimports = [
    name
    for name in collect_submodules("lokdocsqr")
    if not name.startswith("lokdocsqr.gui")
]

datas = [
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "NOTICE"), "."),
    (str(ROOT / "THIRD-PARTY-NOTICES"), "."),
    (str(ROOT / "README.md"), "."),
]

a = Analysis(
    [str(ROOT / "packaging" / "pyinstaller_cli.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "lokdocsqr.gui",
        "PySide6",
        "PyQt5",
        "PyQt6",
        "tkinter",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="lokdocsqr",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="lokdocsqr",
)
