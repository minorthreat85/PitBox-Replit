"""Single source of truth for PitBox version. Reads from version.txt at repo root."""

from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "version.txt"

def _read_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "0.0.0"

__version__ = _read_version()
