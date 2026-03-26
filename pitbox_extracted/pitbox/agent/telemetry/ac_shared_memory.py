"""
Assetto Corsa shared memory reader (Physics, Graphics, Static).
Uses Windows named shared memory: acpmf_physics, acpmf_graphics, acpmf_static.
If AC is not running or mapping fails, returns None and does not crash.
"""
import ctypes
import logging
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Windows constants
FILE_MAP_READ = 0x0004
PAGE_READONLY = 0x02
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# Sizes from AC shared memory reference (#pragma pack(4))
# Physics: ~300 bytes; Graphics/Static have wchar arrays so larger
PHYSICS_SIZE = 512
GRAPHICS_SIZE = 1024
STATIC_SIZE = 1024


def _is_windows() -> bool:
    return sys.platform == "win32"


def _open_mapping(name: str, size: int) -> Optional[tuple[Any, Any]]:
    """Open existing named shared memory. Returns (handle, view_ptr) or None."""
    if not _is_windows():
        return None
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    # AC uses "acpmf_physics" etc.; Windows often has "Local\\" prefix
    for tag in (name, f"Local\\{name}"):
        try:
            h = kernel32.OpenFileMappingW(FILE_MAP_READ, False, tag)
            if h is None or h == 0 or h == INVALID_HANDLE_VALUE:
                continue
            ptr = kernel32.MapViewOfFile(h, FILE_MAP_READ, 0, 0, size)
            if ptr is None or ptr == 0:
                kernel32.CloseHandle(h)
                continue
            return (h, ptr)
        except Exception:
            continue
    return None


def _close_mapping(handle: Any, view_ptr: Any) -> None:
    if not _is_windows() or handle is None:
        return
    try:
        ctypes.windll.kernel32.UnmapViewOfFile(view_ptr)  # type: ignore[attr-defined]
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    except Exception:
        pass


class SharedMemoryReader:
    """
    Read Assetto Corsa shared memory blocks. start()/stop() open/close mappings.
    read_snapshot() returns a dict with normalized_pos, speed_kmh, gear, rpm, throttle,
    brake, lap, lap_time_ms, best_lap_ms, last_lap_ms, sector, sector_time_ms, in_pit,
    and optional track_id, layout, car_model, driver_name, world_xyz.
    Returns None if AC not running or mapping fails; telemetry.ok should be set false.
    """

    def __init__(self) -> None:
        self._physics_handle: Any = None
        self._physics_view: Any = None
        self._graphics_handle: Any = None
        self._graphics_view: Any = None
        self._static_handle: Any = None
        self._static_view: Any = None
        self._started = False

    def start(self) -> None:
        """Open shared memory mappings. No-op if not Windows or already started."""
        if not _is_windows():
            logger.debug("AC shared memory is Windows-only; skipping")
            return
        if self._started:
            return
        self._started = True
        # Open on first read_snapshot to avoid holding handles when AC is closed
        logger.debug("AC shared memory reader started (lazy open)")

    def stop(self) -> None:
        """Close any open mappings."""
        self._close_all()
        self._started = False
        logger.debug("AC shared memory reader stopped")

    def _close_all(self) -> None:
        if self._physics_handle is not None:
            _close_mapping(self._physics_handle, self._physics_view)
            self._physics_handle = self._physics_view = None
        if self._graphics_handle is not None:
            _close_mapping(self._graphics_handle, self._graphics_view)
            self._graphics_handle = self._graphics_view = None
        if self._static_handle is not None:
            _close_mapping(self._static_handle, self._static_view)
            self._static_handle = self._static_view = None

    def _ensure_physics(self) -> bool:
        if self._physics_view is not None:
            return True
        r = _open_mapping("acpmf_physics", PHYSICS_SIZE)
        if r is None:
            return False
        self._physics_handle, self._physics_view = r
        return True

    def _ensure_graphics(self) -> bool:
        if self._graphics_view is not None:
            return True
        r = _open_mapping("acpmf_graphics", GRAPHICS_SIZE)
        if r is None:
            return False
        self._graphics_handle, self._graphics_view = r
        return True

    def _ensure_static(self) -> bool:
        if self._static_view is not None:
            return True
        r = _open_mapping("acpmf_static", STATIC_SIZE)
        if r is None:
            return False
        self._static_handle, self._static_view = r
        return True

    def read_snapshot(self) -> Optional[dict[str, Any]]:
        """
        Read one snapshot from Physics + Graphics + Static.
        Returns dict with: normalized_pos, speed_kmh, gear, rpm, throttle, brake,
        lap, lap_time_ms, best_lap_ms, last_lap_ms, sector, sector_time_ms, in_pit,
        track_id, layout, car_model, driver_name, world_x, world_y, world_z.
        Returns None if AC not running or any mapping fails; caller sets telemetry.ok=false.
        """
        if not _is_windows() or not self._started:
            return None

        # Try to (re)open mappings if needed
        if not self._ensure_physics():
            self._close_all()
            return None
        if not self._ensure_graphics():
            self._close_all()
            return None
        if not self._ensure_static():
            self._close_all()
            return None

        try:
            # SPageFilePhysics layout (pack 4): packetId(0), gas(4), brake(8), fuel(12), gear(16), rpms(20), steer(24), speedKmh(28)
            buf_p = (ctypes.c_char * PHYSICS_SIZE).from_address(self._physics_view)
            raw_p = bytearray(buf_p.raw[:40])
            if len(raw_p) < 36:
                return None
            import struct
            gear = struct.unpack_from("<i", raw_p, 16)[0]
            rpms = struct.unpack_from("<i", raw_p, 20)[0]
            speed_kmh = struct.unpack_from("<f", raw_p, 28)[0]
            gas = struct.unpack_from("<f", raw_p, 4)[0]
            brake = struct.unpack_from("<f", raw_p, 8)[0]

            # SPageFileGraphic: after wchar currentTime/lastTime/bestTime/split (15*2*4=120), at 132: completedLaps, position, iCurrentTime, iLastTime, iBestTime
            # then sessionTimeLeft, distanceTraveled, isInPit@160, currentSectorIndex@164, lastSectorTime@168, numberOfLaps, tyreCompound[33]
            # then replayTimeMultiplier, normalizedCarPosition@248, carCoordinates@252
            buf_g = (ctypes.c_char * GRAPHICS_SIZE).from_address(self._graphics_view)
            raw_g = bytearray(buf_g.raw[:260])
            if len(raw_g) < 260:
                return None
            completed_laps = struct.unpack_from("<i", raw_g, 132)[0]
            i_current = struct.unpack_from("<i", raw_g, 140)[0]
            i_last = struct.unpack_from("<i", raw_g, 144)[0]
            i_best = struct.unpack_from("<i", raw_g, 148)[0]
            is_in_pit = struct.unpack_from("<i", raw_g, 160)[0]
            current_sector = struct.unpack_from("<i", raw_g, 164)[0]
            last_sector_time = struct.unpack_from("<i", raw_g, 168)[0]
            norm_pos = struct.unpack_from("<f", raw_g, 248)[0]
            cx = struct.unpack_from("<f", raw_g, 252)[0]
            cy = struct.unpack_from("<f", raw_g, 256)[0]
            cz = struct.unpack_from("<f", raw_g, 260)[0]

            # SPageFileStatic: carModel@offset after smVersion(30), acVersion(30) -> 60, then numberOfSessions(4), numCars(4), carModel(66) at 68
            # Actually: smVersion wchar[15]=30, acVersion wchar[15]=30, then int numberOfSessions, int numCars, wchar carModel[33]=66
            # So carModel at 60+8 = 68, track at 68+66 = 134, playerName at 134+66 = 200
            buf_s = (ctypes.c_char * STATIC_SIZE).from_address(self._static_view)
            raw_s = bytearray(buf_s.raw[:270])
            if len(raw_s) < 266:
                car_model = ""
                track_id = ""
                driver_name = ""
            else:
                car_model = raw_s[68:68+66].decode("utf-16-le", errors="ignore").strip("\x00").strip()
                track_id = raw_s[134:134+66].decode("utf-16-le", errors="ignore").strip("\x00").strip()
                driver_name = raw_s[200:200+66].decode("utf-16-le", errors="ignore").strip("\x00").strip()

            return {
                "normalized_pos": max(0.0, min(1.0, norm_pos)),
                "speed_kmh": max(0.0, speed_kmh),
                "gear": gear,
                "rpm": max(0, rpms),
                "throttle": max(0.0, min(1.0, gas)),
                "brake": max(0.0, min(1.0, brake)),
                "lap": max(0, completed_laps + 1),
                "lap_time_ms": i_current if i_current > 0 else None,
                "best_lap_ms": i_best if i_best > 0 else None,
                "last_lap_ms": i_last if i_last > 0 else None,
                "sector": max(0, current_sector),
                "sector_time_ms": last_sector_time if last_sector_time > 0 else None,
                "in_pit": bool(is_in_pit),
                "track_id": track_id or "",
                "layout": "",  # Static has track name only; layout might be in Graphics or we leave blank
                "car_model": car_model or "",
                "driver_name": driver_name or "",
                "world_x": cx,
                "world_y": cy,
                "world_z": cz,
            }
        except Exception as e:
            logger.debug("AC shared memory read error: %s", e)
            self._close_all()
            return None
