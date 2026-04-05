# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for PitBox Controller
Builds a windowless (console=False) executable for running as a Windows Service
"""

from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs

block_cipher = None

_controller_submodules = collect_submodules('controller')
_ice_imports = [
    'Ice',
    'IcePy',
    'Glacier2',
    'IceBox',
]
_uvicorn_imports = [
    'uvicorn',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.http.auto',
    'uvicorn.loops.auto',
]

a = Analysis(
    ['controller/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('controller/static', 'static'),
        ('controller/MumbleServer.ice', 'controller'),
        ('examples/controller_config.json', '.'),
    ],
    hiddenimports=_uvicorn_imports + _controller_submodules + _ice_imports,
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
    name='PitBoxController',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # CRITICAL: No console window (windowless for service)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
