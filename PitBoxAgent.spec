# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for PitBox Agent
Builds a windowless (console=False) executable for running as a Windows Service
"""
from PyInstaller.utils.hooks import collect_submodules

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

_uvicorn_imports = [
    'uvicorn',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.protocols.http.auto',
    'uvicorn.loops.auto',
    'websockets',
    # Note: do NOT add 'websockets.asyncio.client' — that path only exists
    # in newer websockets layouts; we pin 13.1, where the public API is
    # `websockets.connect` (legacy module). PyInstaller previously logged
    # an ERROR resolving the missing submodule.
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
    binaries=[],
    datas=[
        ('examples/agent_config.Sim1.json', '.'),
        # version.txt MUST sit at the bundle root — see PitBoxController.spec
        # for the same reasoning. Otherwise the agent reports v0.0.0.
        ('version.txt', '.'),
    ],
    hiddenimports=_uvicorn_imports + _telemetry_imports + _agent_submodules + _pitbox_common_submodules,
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
