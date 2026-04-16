"""
Read and parse Assetto Corsa race_out.json from Documents\\Assetto Corsa\\out.
Returns normalized results for the sim results modal.
"""
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _default_race_out_path() -> Path:
    """Derive Documents\\Assetto Corsa\\out\\race_out.json under the current user's profile.
    Falls back to the legacy hardcoded 'info' user path only if nothing else can be derived."""
    up = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    try:
        return Path(up) / "Documents" / "Assetto Corsa" / "out" / "race_out.json"
    except Exception:
        return Path(r"C:\Users\info\Documents\Assetto Corsa\out\race_out.json")


# Kept for backwards compatibility with anything that imports the name.
DEFAULT_RACE_OUT_PATH = _default_race_out_path()


def get_race_out_path(config: Any) -> Path:
    """Resolve race_out.json path: config ac_out_dir/race_out.json, else current-user default."""
    try:
        from agent.config import get_ac_out_dir
        out_dir = get_ac_out_dir(config)
        if out_dir:
            return out_dir / "race_out.json"
    except Exception:
        pass
    return _default_race_out_path()


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


def _ms_to_total_str(ms: Any) -> str:
    """
    Convert milliseconds total race time to readable string.
    Under 1 hour: 'MM:SS'. 1+ hours: 'H:MM:SS'. Invalid/zero -> '—'.
    """
    if ms is None:
        return "—"
    try:
        n = int(float(ms))
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    total_s = n // 1000
    hours = total_s // 3600
    minutes = (total_s % 3600) // 60
    seconds = total_s % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _fmt_car_name(raw: str) -> str:
    """
    Format a raw AC car model id into a readable name.
    e.g. 'ferrari_458_italia' -> 'Ferrari 458 Italia'
         'ks_porsche_911_gt3_r' -> 'Porsche 911 Gt3 R' (drops 'ks_' prefix)
    """
    if not raw or not isinstance(raw, str):
        return "—"
    s = raw.strip()
    # Drop common prefix tags: ks_, rss_, sc_, etc.
    s = re.sub(r'^(?:ks|rss|sc|rm|ta|gt|ruf|ac)_', '', s, flags=re.IGNORECASE)
    return s.replace('_', ' ').title()


def _first(*values: Any) -> Any:
    """Return the first non-None, non-empty value."""
    for v in values:
        if v is not None and v != "":
            return v
    return None


_AC_SESSION_TYPE_MAP = {1: "PRACTICE", 2: "QUALIFY", 3: "RACE", 4: "HOTLAP", 5: "TIME_ATTACK", 6: "DRIFT", 7: "DRAG"}


def _parse_ac_native(data: dict) -> Optional[dict[str, Any]]:
    """Parse the native AC race_out.json format: top-level `players` + `sessions` with raceResult/bestLaps/lapstotal.
    Returns the parsed result dict or None if this is not the AC native shape."""
    players = data.get("players")
    sessions = data.get("sessions")
    if not isinstance(players, list) or not isinstance(sessions, list) or not sessions:
        return None

    # Track
    track_raw = data.get("track")
    track_name = (track_raw.strip() if isinstance(track_raw, str) else "") or "—"

    # Pick the most relevant session: last one that has a non-empty raceResult; else last.
    chosen = None
    for sess in reversed(sessions):
        if isinstance(sess, dict) and isinstance(sess.get("raceResult"), list) and sess.get("raceResult"):
            chosen = sess
            break
    if chosen is None:
        chosen = sessions[-1] if isinstance(sessions[-1], dict) else {}

    # Session type (RACE/QUALIFY/PRACTICE)
    session_type = ""
    t_int = chosen.get("type")
    if isinstance(t_int, (int, float)):
        session_type = _AC_SESSION_TYPE_MAP.get(int(t_int), "")
    if not session_type:
        name_str = chosen.get("name")
        if isinstance(name_str, str) and name_str.strip():
            session_type = name_str.strip().upper()

    # Configured laps
    total_laps_raw = chosen.get("lapsCount")
    try:
        total_laps: Optional[int] = int(total_laps_raw) if total_laps_raw is not None else None
    except (TypeError, ValueError):
        total_laps = None
    if total_laps == 0:
        total_laps = None  # timed race

    race_order = chosen.get("raceResult") if isinstance(chosen.get("raceResult"), list) else []
    best_laps = chosen.get("bestLaps") if isinstance(chosen.get("bestLaps"), list) else []
    laps_total = chosen.get("lapstotal") if isinstance(chosen.get("lapstotal"), list) else []

    # If no raceResult, fall back to player index order
    order = race_order if race_order else list(range(len(players)))

    # Build per-player best lap lookup: bestLaps is typically aligned to player index.
    def _best_lap_ms(pidx: int) -> Optional[int]:
        if 0 <= pidx < len(best_laps):
            v = best_laps[pidx]
            if isinstance(v, (int, float)) and int(v) > 0:
                return int(v)
        return None

    def _laps_done(pidx: int) -> Optional[int]:
        if 0 <= pidx < len(laps_total):
            v = laps_total[pidx]
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
        return None

    # Leader best lap for gap calc (fallback when total time unavailable)
    leader_best_ms: Optional[int] = None
    if order:
        leader_best_ms = _best_lap_ms(int(order[0]) if isinstance(order[0], (int, float)) else 0)

    results: list[dict[str, Any]] = []
    ai_counter = 0
    for pos_idx, pidx_any in enumerate(order):
        try:
            pidx = int(pidx_any)
        except (TypeError, ValueError):
            continue
        if not (0 <= pidx < len(players)):
            continue
        p = players[pidx] if isinstance(players[pidx], dict) else {}
        name = p.get("name")
        driver = name.strip() if isinstance(name, str) and name.strip() else ""
        if not driver:
            ai_counter += 1
            driver = f"AI {ai_counter}"
        car_raw_val = p.get("car")
        car_raw = car_raw_val.strip() if isinstance(car_raw_val, str) else ""
        car = _fmt_car_name(car_raw) if car_raw else "—"

        best_ms = _best_lap_ms(pidx)
        lap = _ms_to_lap_str(best_ms) if best_ms is not None else "—"
        laps = _laps_done(pidx)

        # Gap: approximate using best-lap delta (AC native format has no cumulative race time).
        if pos_idx == 0 or best_ms is None or leader_best_ms is None:
            gap = "—"
        else:
            delta = best_ms - leader_best_ms
            gap = f"+{_ms_to_lap_str(delta)}" if delta > 0 else "—"

        results.append({
            "pos": pos_idx + 1,
            "driver": driver,
            "car": car,
            "car_raw": car_raw,
            "laps": laps,
            "lap": lap,
            "total_time": "—",
            "gap": gap,
        })

    return {
        "results": results,
        "track_name": track_name,
        "session_type": session_type,
        "total_laps": total_laps,
    }


def parse_race_out(race_out_path: Path) -> Optional[dict[str, Any]]:
    """
    Parse AC race_out.json into:
      {
        "results": [{ pos, driver, car, car_raw, laps, lap, total_time, gap }, ...],
        "track_name": str,
        "session_type": str,   # "RACE" | "QUALIFY" | "PRACTICE" | ""
        "total_laps": int|None,
      }
    Handles both the AC native shape (top-level `players`+`sessions`) and the legacy
    `leaderboardLines`/`LeaderBoard` shape. Returns None if missing/invalid.
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

    # Preferred: native AC format
    native = _parse_ac_native(data)
    if native is not None:
        return native

    # Legacy fallback: leaderboardLines / LeaderBoard / nested SessionResult
    session_type = str(
        _first(data.get("Type"), data.get("type"), data.get("SessionType"), "") or ""
    ).strip().upper()

    total_laps_raw = _first(data.get("RaceLaps"), data.get("race_laps"), data.get("Laps"))
    try:
        total_laps: Optional[int] = int(total_laps_raw) if total_laps_raw is not None else None
    except (TypeError, ValueError):
        total_laps = None

    track_name = (
        _first(
            data.get("trackName"),
            data.get("TrackName"),
            data.get("track_name"),
            data.get("track"),
        )
        or "—"
    )
    if isinstance(track_name, str):
        track_name = track_name.strip() or "—"
    else:
        track_name = "—"

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
        return {"results": [], "track_name": track_name, "session_type": session_type, "total_laps": total_laps}

    results = []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue

        # Nested AC format: { "car": {...}, "timing": {...} }
        car_dict = raw.get("car") if isinstance(raw.get("car"), dict) else {}
        timing_dict = raw.get("timing") if isinstance(raw.get("timing"), dict) else {}

        # Position
        pos = _first(raw.get("position"), raw.get("pos"), raw.get("Position"), i + 1)
        if isinstance(pos, (int, float)):
            pos = int(pos)
        else:
            pos = i + 1

        # Driver name: check nested car dict first, then flat
        driver = _first(
            car_dict.get("DriverName"),
            car_dict.get("driverName"),
            raw.get("driverName"),
            raw.get("driver_name"),
            raw.get("name"),
            raw.get("driver"),
            raw.get("DriverName"),
        )
        driver = (driver or "—").strip() if isinstance(driver, str) else "—"

        # Car model: nested car dict preferred
        car_raw_val = _first(
            car_dict.get("Model"),
            car_dict.get("model"),
            raw.get("carModel"),
            raw.get("car_model"),
            raw.get("model"),
        )
        car_raw = (car_raw_val or "").strip() if isinstance(car_raw_val, str) else ""
        car = _fmt_car_name(car_raw) if car_raw else "—"

        # Best lap: nested timing dict preferred
        best_raw = _first(
            timing_dict.get("BestLap"),
            timing_dict.get("bestLap"),
            raw.get("bestLap"),
            raw.get("best_lap"),
            raw.get("lapTime"),
            raw.get("bestLapTime"),
        )
        if best_raw is not None and isinstance(best_raw, (int, float)) and int(best_raw) < 0:
            lap = "—"
        else:
            lap = _ms_to_lap_str(best_raw) if isinstance(best_raw, (int, float)) else (str(best_raw).strip() if best_raw else "—")

        # Laps completed
        laps_raw = _first(
            timing_dict.get("LapCount"),
            timing_dict.get("lapCount"),
            raw.get("lapCount"),
            raw.get("laps"),
            raw.get("Laps"),
            raw.get("numLaps"),
        )
        try:
            laps: Optional[int] = int(laps_raw) if laps_raw is not None else None
        except (TypeError, ValueError):
            laps = None

        # Total race time
        total_raw = _first(
            timing_dict.get("TotalTime"),
            timing_dict.get("totalTime"),
            raw.get("totalTime"),
            raw.get("total_time"),
            raw.get("TotalTime"),
        )
        total_time = _ms_to_total_str(total_raw)

        # Gap to leader
        gap_raw = _first(
            raw.get("gap"),
            raw.get("Gap"),
            raw.get("gapToLeader"),
            raw.get("gap_to_leader"),
        )
        if gap_raw is None or gap_raw == "":
            gap = "—"
        elif isinstance(gap_raw, (int, float)):
            g = int(gap_raw)
            if g <= 0 and pos == 1:
                gap = "—"
            else:
                gap = f"+{_ms_to_lap_str(gap_raw)}" if g > 0 else "—"
        else:
            gap = str(gap_raw).strip() or "—"

        results.append({
            "pos": pos,
            "driver": driver,
            "car": car,
            "car_raw": car_raw,
            "laps": laps,
            "lap": lap,
            "total_time": total_time,
            "gap": gap,
        })

    return {
        "results": results,
        "track_name": track_name,
        "session_type": session_type,
        "total_laps": total_laps,
    }
