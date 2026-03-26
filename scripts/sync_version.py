#!/usr/bin/env python3
"""Sync version from pitbox_common/version.py to version.ini for Inno Setup."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_PY = ROOT / "pitbox_common" / "version.py"
VERSION_INI = ROOT / "version.ini"
VERSION_FILE = ROOT / "VERSION"
VERSION_TXT = ROOT / "version.txt"


def get_version() -> str:
    """Extract __version__ from version.py."""
    content = VERSION_PY.read_text(encoding="utf-8")
    for line in content.splitlines():
        if line.strip().startswith("__version__"):
            # __version__ = "0.1.0"
            parts = line.split("=", 1)
            if len(parts) == 2:
                return parts[1].strip().strip('"\'')
    raise ValueError("Could not find __version__ in version.py")


def main() -> int:
    version = get_version()
    VERSION_INI.write_text(
        f"[Version]\nVersion={version}\n",
        encoding="utf-8",
    )
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    VERSION_TXT.write_text(version + "\n", encoding="utf-8")
    print(f"Synced version {version} to version.ini, VERSION, and version.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
