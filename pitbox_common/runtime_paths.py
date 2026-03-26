"""Shared PitBox runtime paths (controller config, logs, dir)."""

import os
from pathlib import Path


def controller_dir() -> Path:
    """Base directory for controller data: %APPDATA%/PitBox/Controller (Windows) or ~/.config/PitBox/Controller (Unix)."""
    if os.name == "nt":
        base = os.environ.get("APPDATA", "") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", "") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "PitBox" / "Controller"


def controller_config_path() -> Path:
    """Canonical path for controller_config.json: controller_dir() / controller_config.json."""
    return controller_dir() / "controller_config.json"


def controller_data_dir() -> Path:
    """Directory for controller data (e.g. enrolled_rigs backup): controller_dir() / data."""
    return controller_dir() / "data"


def controller_logs_dir() -> Path:
    """Directory for controller log files: controller_dir() / logs."""
    return controller_dir() / "logs"
