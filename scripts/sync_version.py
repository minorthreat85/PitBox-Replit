#!/usr/bin/env python3
"""Sync version from version.txt to version.ini (for Inno Setup) and VERSION file."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_TXT = ROOT / "version.txt"
VERSION_INI = ROOT / "version.ini"
VERSION_FILE = ROOT / "VERSION"


def get_version() -> str:
    """Read version from version.txt (single source of truth)."""
    return VERSION_TXT.read_text(encoding="utf-8").strip()


def main() -> int:
    version = get_version()
    VERSION_INI.write_text(
        f"[Version]\nVersion={version}\n",
        encoding="utf-8",
    )
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    print(f"Synced version {version} to version.ini and VERSION")
    return 0


if __name__ == "__main__":
    sys.exit(main())
