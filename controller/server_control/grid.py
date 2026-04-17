"""Native ``entry_list.ini`` grid manipulation.

Reverse / swap operations on an Assetto Corsa preset's ``entry_list.ini``
file. Adapted (logic only) from the upstream ``ac-websocket-server``
package's ``entries.py`` / ``grid.py``; PitBox owns the file I/O and
preserves all original keys verbatim (including custom ones like
``BALLAST``, ``RESTRICTOR``, ``FIXED_SETUP``, ``SPECTATOR_MODE`` and any
mod-server-specific extensions).

Operations write atomically: the new content is staged to
``entry_list.ini.new``, the existing file is rotated to
``entry_list.ini.old``, then the staged file replaces the live one.
acServer.exe re-reads ``entry_list.ini`` only at start-up, so callers
must restart the server (or wait for the next session) for the new grid
to take effect.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, List, Tuple

logger = logging.getLogger(__name__)

ENTRY_SECTION_RE = re.compile(r"^\s*\[CAR_(\d+)\]\s*$", re.IGNORECASE)
SECTION_RE = re.compile(r"^\s*\[(.+?)\]\s*$")

_grid_lock = threading.Lock()


class GridError(RuntimeError):
    """Raised for grid-file manipulation failures."""


# ---------------------------------------------------------------------- #
# Parsing
# ---------------------------------------------------------------------- #
def _parse_entry_list(path: Path) -> Tuple[List[str], "OrderedDict[int, List[str]]"]:
    """Parse ``entry_list.ini`` into (preamble_lines, {car_id: section_lines}).

    ``section_lines`` keeps the original ``[CAR_N]`` header as the first
    element and every subsequent line up to (but not including) the next
    section header. This preserves blank lines, comments, and key order.
    """
    if not path.exists():
        raise GridError(f"entry_list.ini not found: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # AC INIs occasionally drift to cp1252 on legacy installs.
        text = path.read_text(encoding="cp1252")

    lines = text.splitlines(keepends=True)

    preamble: List[str] = []
    sections: "OrderedDict[int, List[str]]" = OrderedDict()
    current_id: int | None = None
    current_lines: List[str] = []
    seen_any_section = False

    def _flush() -> None:
        if current_id is None:
            return
        if current_id in sections:
            raise GridError(f"Duplicate [CAR_{current_id}] section in {path}")
        sections[current_id] = current_lines

    for raw in lines:
        m_car = ENTRY_SECTION_RE.match(raw)
        if m_car:
            _flush()
            current_id = int(m_car.group(1))
            current_lines = [raw]
            seen_any_section = True
            continue
        m_other = SECTION_RE.match(raw)
        if m_other and not m_car:
            # Non-CAR_ section: treat as preamble (e.g. comments, custom
            # blocks). PitBox doesn't expect any but we preserve them.
            _flush()
            current_id = None
            current_lines = []
            preamble.append(raw)
            seen_any_section = True
            continue
        if current_id is None:
            preamble.append(raw)
        else:
            current_lines.append(raw)

    _flush()

    if not sections:
        raise GridError(f"No [CAR_N] sections found in {path}")
    if not seen_any_section:
        raise GridError(f"entry_list.ini at {path} has no INI sections")

    return preamble, sections


def _renumber_and_serialise(
    preamble: List[str], ordered: Iterable[Tuple[int, List[str]]]
) -> str:
    """Rewrite section headers to match the new ordinal positions."""
    out: List[str] = list(preamble)
    for new_idx, (_old_id, body_lines) in enumerate(ordered):
        body_copy = list(body_lines)
        # Replace the first line (the original [CAR_N] header) with the
        # new ordinal. Preserve the line ending of the original header.
        first = body_copy[0]
        eol = ""
        for suffix in ("\r\n", "\n", "\r"):
            if first.endswith(suffix):
                eol = suffix
                break
        body_copy[0] = f"[CAR_{new_idx}]" + eol
        out.extend(body_copy)
    return "".join(out)


# ---------------------------------------------------------------------- #
# Atomic write
# ---------------------------------------------------------------------- #
def _atomic_write(path: Path, content: str) -> None:
    new_path = path.with_suffix(path.suffix + ".new")
    old_path = path.with_suffix(path.suffix + ".old")

    new_path.write_text(content, encoding="utf-8")

    try:
        if path.exists():
            if old_path.exists():
                old_path.unlink()
            shutil.copy2(path, old_path)
        os.replace(new_path, path)
    except OSError as exc:
        # Best-effort cleanup of the staged file
        try:
            if new_path.exists():
                new_path.unlink()
        except OSError:
            pass
        raise GridError(f"Failed to replace {path}: {exc}") from exc


# ---------------------------------------------------------------------- #
# Public ops
# ---------------------------------------------------------------------- #
def reverse_grid(entry_list_path: Path) -> dict:
    """Reverse the order of all ``[CAR_N]`` entries.

    Returns a summary dict ``{path, count, before, after}`` where
    ``before`` / ``after`` are lists of original car_id ordinals.
    """
    with _grid_lock:
        preamble, sections = _parse_entry_list(entry_list_path)
        before = list(sections.keys())
        items = list(sections.items())
        items.reverse()
        content = _renumber_and_serialise(preamble, items)
        _atomic_write(entry_list_path, content)
        after = [item[0] for item in items]
        logger.info(
            "server_control.grid: reversed %s (%d entries) before=%s after=%s",
            entry_list_path, len(items), before, after,
        )
        return {
            "path": str(entry_list_path),
            "count": len(items),
            "before": before,
            "after": after,
        }


def swap_grid(entry_list_path: Path, slot_a: int, slot_b: int) -> dict:
    """Swap the two grid slots ``slot_a`` and ``slot_b`` (0-indexed).

    Operates on grid *positions*, not on the original CAR_N ids: this
    matches how operators think about the grid ("swap pole with P2").
    """
    if slot_a == slot_b:
        raise GridError(f"swap_grid: slot_a == slot_b ({slot_a})")

    with _grid_lock:
        preamble, sections = _parse_entry_list(entry_list_path)
        items = list(sections.items())
        n = len(items)
        if not (0 <= slot_a < n and 0 <= slot_b < n):
            raise GridError(
                f"swap_grid: slot indices out of range (have {n} entries, got {slot_a}/{slot_b})"
            )
        before = [item[0] for item in items]
        items[slot_a], items[slot_b] = items[slot_b], items[slot_a]
        content = _renumber_and_serialise(preamble, items)
        _atomic_write(entry_list_path, content)
        after = [item[0] for item in items]
        logger.info(
            "server_control.grid: swapped %s slots %d<->%d before=%s after=%s",
            entry_list_path, slot_a, slot_b, before, after,
        )
        return {
            "path": str(entry_list_path),
            "count": n,
            "swapped": [slot_a, slot_b],
            "before": before,
            "after": after,
        }


def list_grid(entry_list_path: Path) -> list[dict]:
    """Return a lightweight grid summary for the UI.

    Each item: ``{slot, car_id_original, model, drivername, guid,
    skin}``. Slot is the current 0-indexed position in the file.
    """
    _, sections = _parse_entry_list(entry_list_path)
    out: list[dict] = []
    for slot, (car_id, body_lines) in enumerate(sections.items()):
        info = {"slot": slot, "car_id_original": car_id}
        for raw in body_lines[1:]:
            line = raw.strip()
            if not line or line.startswith(";") or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip().upper()
            val = val.strip()
            if key in {"MODEL", "SKIN", "DRIVERNAME", "TEAM", "GUID", "BALLAST", "RESTRICTOR"}:
                info[key.lower()] = val
        out.append(info)
    return out
