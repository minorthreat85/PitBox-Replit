# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for PitBox Controller
Builds a windowless (console=False) executable for running as a Windows Service
"""

import os
import sys
from PyInstaller.utils.hooks import collect_submodules

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
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.protocols.http.auto',
    'uvicorn.loops.auto',
    'websockets',
]

# Belt-and-suspenders: explicitly list the vendored timing/server-control
# modules so PyInstaller never optimises them out, even if a future
# version of `collect_submodules` misses package nesting. These packages
# are also picked up by `collect_submodules('controller')` above.
_pitbox_imports = [
    'controller.timing',
    'controller.timing.constants',
    'controller.timing.engine',
    'controller.timing.vendor',
    'controller.timing.vendor.acudpclient',
    'controller.timing.vendor.acudpclient.client',
    'controller.timing.vendor.acudpclient.exceptions',
    'controller.timing.vendor.acudpclient.packet_base',
    'controller.timing.vendor.acudpclient.packets',
    'controller.timing.vendor.acudpclient.protocol',
    'controller.timing.vendor.acudpclient.types',
    'controller.server_control',
    'controller.server_control.adapter',
    'controller.server_control.grid',
    # Note: controller.api_server_control_routes and
    # controller.api_timing_routes are intentionally NOT listed here.
    # They are picked up by collect_submodules('controller') above; listing
    # them again caused PyInstaller's hidden-import resolver to emit a
    # spurious "not found" ERROR (the resolver runs before the controller
    # path is fully indexed for some module orderings).
]

# Bundle slice2py.exe so Ice.loadSlice() can find it in the frozen bundle.
# It lives in the same Scripts/ directory as the build Python.
_slice2py = os.path.join(os.path.dirname(sys.executable), 'slice2py.exe')
_ice_binaries = [(_slice2py, '.')] if os.path.exists(_slice2py) else []

# Bundle the Ice system .ice slice files (e.g. Ice/SliceChecksumDict.ice).
# MumbleServer.ice includes <Ice/SliceChecksumDict.ice>, so slice2py needs
# these at runtime.  Find them from the installed zeroc-ice package.
_ice_slice_src = None
try:
    import Ice as _Ice
    _ice_pkg = os.path.dirname(_Ice.__file__)
    _slice_candidates = [
        os.path.join(_ice_pkg, 'slice'),
        os.path.join(_ice_pkg, '..', 'slice'),
        os.path.join(_ice_pkg, '..', '..', 'slice'),
        os.path.join(sys.prefix, 'slice'),
        os.path.join(sys.prefix, 'Lib', 'site-packages', 'slice'),
    ]
    for _c in _slice_candidates:
        _n = os.path.normpath(_c)
        if os.path.isdir(_n) and os.path.exists(os.path.join(_n, 'Ice')):
            _ice_slice_src = _n
            break
except Exception:
    pass

_datas = [
    ('controller/static', 'static'),
    ('controller/MumbleServer.ice', 'controller'),
    ('examples/controller_config.json', '.'),
]
if _ice_slice_src:
    _datas.append((_ice_slice_src, 'slice'))

a = Analysis(
    ['controller/main.py'],
    pathex=[],
    binaries=_ice_binaries,
    datas=_datas,
    hiddenimports=_uvicorn_imports + _pitbox_imports + _controller_submodules + _ice_imports,
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
