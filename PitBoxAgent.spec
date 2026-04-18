# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for PitBox Agent
Builds a windowless (console=False) executable for running as a Windows Service
"""

block_cipher = None

a = Analysis(
    ['agent/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('examples/agent_config.Sim1.json', '.'),
    ],
    hiddenimports=[
        'uvicorn',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.http.auto',
        'uvicorn.loops.auto',
        'agent',
        'agent.auth',
        'agent.config',
        'agent.routes',
        'agent.process_manager',
        'agent.controller_heartbeat',
        'agent.beacon',
        'agent.kiosk_apply',
        'agent.race_ini',
        'agent.utils.files',
        'agent.utils.cmpreset',
        'agent.mumble_client',
        'agent.identity',
        'agent.logging_config',
        'agent.telemetry',
        'agent.telemetry.sm_reader',
        'agent.telemetry.sender',
        'websockets',
        'websockets.asyncio.client',
    ],
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
