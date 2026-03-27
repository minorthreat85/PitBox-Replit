# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for PitBox Tray Launcher
Builds a windowed (no console) EXE with the custom PitBox icon.

pystray uses dynamic backend loading (pystray._win32 on Windows) so we must
use collect_all() to pull in every sub-module; otherwise PyInstaller misses them.
"""

from PyInstaller.utils.hooks import collect_all

block_cipher = None

pystray_datas, pystray_binaries, pystray_hiddenimports = collect_all('pystray')
pillow_datas, pillow_binaries, pillow_hiddenimports = collect_all('PIL')

a = Analysis(
    ['systray/pitbox_tray.py'],
    pathex=[],
    binaries=[] + pystray_binaries + pillow_binaries,
    datas=[('assets/pitbox.ico', '.')] + pystray_datas + pillow_datas,
    hiddenimports=[] + pystray_hiddenimports + pillow_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='PitBoxTray',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon='assets/pitbox.ico',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
