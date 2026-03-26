"""
Read/write AC server INI files preserving section and key order.
No comment preservation; output is valid AC server_cfg.ini / entry_list.ini.
Atomic write: write to temp file then replace.
"""
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _ini_value(v: Any) -> str:
    """Convert value for INI: bool -> 0/1, keep empty string, else str."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(int(v) if isinstance(v, float) and v == int(v) else v)
    return str(v)


def read_ini(path: Path) -> dict[str, dict[str, str]]:
    """
    Parse INI file into ordered dict: section -> { key -> value }.
    Keys and sections keep file order.
    """
    data: dict[str, dict[str, str]] = {}
    current: str | None = None
    if not path.exists():
        return data
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n\r")
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                current = stripped[1:-1].strip()
                if current not in data:
                    data[current] = {}
            elif current is not None:
                if "=" in stripped:
                    key, _, val = stripped.partition("=")
                    data[current][key.strip()] = val.strip()
                else:
                    data[current][stripped] = ""
    return data


def write_ini(path: Path, data: dict[str, dict[str, str]]) -> None:
    """Write INI file from section -> { key -> value }. Creates parent dirs if needed."""
    write_ini_atomic(path, data)


def write_ini_atomic(path: Path, data: dict[str, dict[str, str]]) -> None:
    """Write INI atomically: write to temp file in same dir then os.replace to target. Preserves all keys; empty string value kept. Releases file lock immediately after replace so start/stop/restart can run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for section, opts in data.items():
        lines.append(f"[{section}]")
        for k, v in opts.items():
            lines.append(f"{k}={v}")
        lines.append("")
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".server_cfg.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    logger.info("Wrote INI: %s", path)


def get_file_revision(path: Path) -> tuple[float, int]:
    """Return (mtime, size) for revision detection."""
    if not path.exists():
        return 0.0, 0
    st = path.stat()
    return st.st_mtime, st.st_size
