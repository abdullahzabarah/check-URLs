# -*- mode: python ; coding: utf-8 -*-

import sys

icon_path = 'url_monitor.icns' if sys.platform == 'darwin' else 'url_monitor.ico'
use_upx = sys.platform == 'win32'

a = Analysis(
    ['check_urls_app.py'],
    pathex=[],
    binaries=[],
    datas=[('urls.txt', '.'), ('url_monitor_logo.svg', '.')],
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
    name='check_urls_app',
    icon=icon_path,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=use_upx,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
