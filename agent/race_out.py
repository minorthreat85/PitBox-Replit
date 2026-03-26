"""
Read and parse Assetto Corsa race_out.json from Documents\\Assetto Corsa\\out.
Returns normalized results for the sim results modal (pos, driver, lap, gap).
"""
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default path when config has no ac_cfg (e.g. single-machine dev)
DEFAULT_RACE_OUT_PATH = Path(r"C:\Users\info\Documents\Assetto Corsa\out\race_out.json")


def get_race_out_path(config: Any) -> Path:
    """Resolve race_out.json path: config ac_out_dir/race_out.json, else DEFAULT_RACE_OUT_PATH."""
    try:
        from agent.config import get_ac_out_dir
        out_dir = get_ac_out_dir(config)
        if out_dir:
            return out_dir / "race_out.json"
    except Exception:
        pass
    return DEFAULT_RACE_OUT_PATH


def _ms_to_lap_str(ms: Any) -> str:
    """Convert milliseconds to 'M:SS.mmm' lap string. Invalid/negative -> '—'."""
    if ms is None:
        return "—"
    try:
        n = int(float(ms))
    except (TypeError, ValueError):
        return "—"
    if n < 0 or n >= 3600000:
        return "—"
    minutes = n // 60000
    remainder_ms = n % 60000
    seconds = remainder_ms // 1000
    millis = remainder_ms % 1000
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def _first(*values: Any) -> Any:
    """Return the first non-None, non-empty value."""
    for v in values:
        if v is not None and v != "":
            return v
    return None


def parse_race_out(race_out_path: Path) -> Optional[dict[str, Any]]:
    """
    Parse AC race_out.json into { "results": [{ pos, driver, lap, gap }, ...], "track_name": str }.
    Tolerates multiple JSON shapes (leaderboardLines, LeaderBoard, etc.). Returns None if missing/invalid.
    """
    if not race_out_path.is_file():
        return None
    data = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with open(race_out_path, "r", encoding=encoding) as f:
                data = json.load(f)
            break
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    if not isinstance(data, dict):
        return None

    # Track name: common keys
    track_name = (
        _first(
            data.get("trackName"),
            data.get("track_name"),
            data.get("track"),
        )
        or "—"
    )
    if isinstance(track_name, str):
        track_name = track_name.strip() or "—"
    else:
        track_name = "—"

    # Leaderboard array: try common keys
    rows = _first(
        data.get("leaderboardLines"),
        data.get("LeaderBoard"),
        data.get("leaderboard"),
        data.get("results"),
    )
    if isinstance(data.get("SessionResult"), dict):
        rows = _first(
            data["SessionResult"].get("LeaderBoard"),
            data["SessionResult"].get("leaderboardLines"),
            data["SessionResult"].get("results"),
            rows,
        )
    if not isinstance(rows, list) or len(rows) == 0:
        return {"results": [], "track_name": track_name}

    results = []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        pos = _first(raw.get("position"), raw.get("pos"), raw.get("Position"), i + 1)
        if isinstance(pos, (int, float)):
            pos = int(pos)
        else:
            pos = i + 1
        driver = _first(
            raw.get("driverName"),
            raw.get("driver_name"),
            raw.get("name"),
            raw.get("driver"),
            raw.get("DriverName"),
        )
        driver = (driver or "—").strip() if isinstance(driver, str) else "—"
        # Best lap: may be ms number or string; -1 = invalid
        best_raw = _first(
            raw.get("bestLap"),
            raw.get("best_lap"),
            raw.get("lapTime"),
            raw.get("bestLapTime"),
        )
        if best_raw is not None and isinstance(best_raw, (int, float)) and int(best_raw) < 0:
            lap = "—"
        else:
            lap = _ms_to_lap_str(best_raw) if isinstance(best_raw, (int, float)) else (str(best_raw).strip() if best_raw else "—")
        gap_raw = _first(
            raw.get("gap"),
            raw.get("Gap"),
            raw.get("gapToLeader"),
            raw.get("gap_to_leader"),
        )
        if gap_raw is None or gap_raw == "":
            gap = "—" if pos > 1 else "—"
        elif isinstance(gap_raw, (int, float)):
            g = int(gap_raw)
            if g <= 0 and pos == 1:
                gap = "—"
            else:
                gap = f"+{_ms_to_lap_str(gap_raw)}" if g > 0 else "—"
        else:
            gap = str(gap_raw).strip() or "—"
        results.append({"pos": pos, "driver": driver, "lap": lap, "gap": gap})

    return {"results": results, "track_name": track_name}
