# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['/home/dsidlo/workspace/honcho/pyinstaller/src/honcho_pi/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[('/home/dsidlo/workspace/honcho/pyinstaller/src/honcho_pi/package_data/pi-extension', 'honcho_pi/package_data/pi-extension'), ('/home/dsidlo/workspace/honcho/pyinstaller/src/honcho_pi/package_data/systemd', 'honcho_pi/package_data/systemd')],
    hiddenimports=['honcho_pi', 'honcho_pi.cli', 'honcho_pi.commands', 'honcho_pi.config', 'honcho_pi.services', 'honcho_pi.pi_integration', 'click', 'rich', 'pydantic', 'typer', 'jinja2', 'toml', 'requests', 'httpx'],
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
