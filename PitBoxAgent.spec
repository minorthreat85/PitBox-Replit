# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for PitBox Agent
Builds a windowless (console=False) executable for running as a Windows Service
"""
from PyInstaller.utils.hooks import collect_submodules, collect_all

block_cipher = None

# Belt-and-suspenders: collect EVERY submodule under `agent` (not just the
# ones explicitly imported at top of agent/main.py). Many features —
# pairing, enrollment_client, sim_display, update_state, hotkey,
# server_cfg_sync, race_out, common.event_log, service.event_emitter — are
# imported lazily inside try/except blocks during startup. If any of them
# is missing from the frozen bundle the feature silently no-ops at runtime
# (no ImportError visible to the user, just a warning in the agent log).
# `collect_submodules` traverses the package tree and picks them all up.
_agent_submodules = collect_submodules('agent')
_pitbox_common_submodules = collect_submodules('pitbox_common')

# CRITICAL: websockets 13.1 uses lazy `__getattr__` in its top-level
# __init__.py to resolve `websockets.connect` to either
# `websockets.asyncio.client` or `websockets.legacy.client` at runtime.
# Listing 'websockets' as a hidden import is NOT enough — PyInstaller will
# only ship the empty top-level package and the agent will log
# "websockets package not available; telemetry disabled" at runtime, even
# though the package is installed in the build venv. We must `collect_all`
# to bring in every submodule (legacy.client, asyncio.client, sync.client,
# extensions, ...). Validated against websockets==13.1 (see requirements.txt).
_ws_datas, _ws_binaries, _ws_hiddenimports = collect_all('websockets')

_uvicorn_imports = [
    'uvicorn',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.protocols.http.auto',
    'uvicorn.loops.auto',
]

# Explicit safety net: telemetry sender + sm_reader are also collected by
# collect_submodules above, but list them so a future refactor that
# breaks the package layout still fails LOUDLY at build time, not at the
# first telemetry frame.
_telemetry_imports = [
    'agent.telemetry',
    'agent.telemetry.sm_reader',
    'agent.telemetry.sender',
]

a = Analysis(
    ['agent/main.py'],
    pathex=[],
    binaries=_ws_binaries,
    datas=[
        ('examples/agent_config.Sim1.json', '.'),
        # version.txt MUST sit at the bundle root — see PitBoxController.spec
        # for the same reasoning. Otherwise the agent reports v0.0.0.
        ('version.txt', '.'),
    ] + _ws_datas,
    hiddenimports=_uvicorn_imports + _telemetry_imports + _agent_submodules + _pitbox_common_submodules + _ws_hiddenimports,
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
    name='PitBoxAgent',
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
