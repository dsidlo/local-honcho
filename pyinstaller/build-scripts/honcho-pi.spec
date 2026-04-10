# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for honcho-pi.

Run from project root: pyinstaller pyinstaller/build-scripts/honcho-pi.spec
"""

from PyInstaller.building.build_main import Analysis, PYZ, EXE
from pathlib import Path

# Project paths (run from project root)
project_root = Path.cwd()
script_dir = project_root / "pyinstaller"
src_dir = script_dir / "src"

# Collect data files from package_data
pkg_data_dir = src_dir / "honcho_pi" / "package_data"
added_files = []

if pkg_data_dir.exists():
    for subdir in ["pi-extension", "systemd"]:
        src_path = pkg_data_dir / subdir
        if src_path.exists():
            added_files.append((str(src_path), f"honcho_pi/package_data/{subdir}"))

# Analysis configuration
a = Analysis(
    [str(src_dir / "honcho_pi" / "__main__.py")],
    pathex=[str(src_dir)],
    binaries=[],
    datas=added_files,
    hiddenimports=[
        'honcho_pi',
        'honcho_pi.cli',
        'honcho_pi.commands',
        'honcho_pi.config',
        'honcho_pi.services',
        'honcho_pi.pi_integration',
        'honcho_pi.commands.self_cmd',
        'honcho_pi.commands.status',
        'honcho_pi.commands.install',
        'honcho_pi.commands.uninstall',
        'honcho_pi.commands.doctor',
        'click',
        'rich',
        'rich.console',
        'rich.table',
        'rich.panel',
        'rich.progress',
        'pydantic',
        'pydantic.v1',
        'typer',
        'typer.core',
        'jinja2',
        'jinja2.runtime',
        'jinja2.loaders',
        'toml',
        'requests',
        'requests.packages',
        'httpx',
        'httpx._transports',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'pytest',
        'unittest',
        'pydoc',
        'doctest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

# Build configuration
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='honcho-pi',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
