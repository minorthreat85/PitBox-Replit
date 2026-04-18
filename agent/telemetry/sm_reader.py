"""
Assetto Corsa Shared Memory reader.

AC exposes three memory-mapped files on Windows when the game is running:
  - Local\\acpmf_physics  - high-frequency physics (gas, brake, gear, rpm, speed, ...)
  - Local\\acpmf_graphics - session/lap/timing state (lap times, position, sector, pit, normalizedCarPosition)
  - Local\\acpmf_static   - one-shot session metadata (track, car model, player name, sector count, ...)

This module opens those mmaps and decodes the leading fields we actually use.
Strings in AC SM are UTF-16LE (wchar) fixed-length arrays terminated by NUL.

Layout reference:
  https://www.assettocorsa.net/forum/index.php?threads/doc-shared-memory-reference.59965/
  Verified against SimHub/CrewChief/Sim Racing Studio implementations.

We deliberately stop parsing each struct after the last field we need - this
keeps us robust to AC version changes that append new fields at the end.
"""
from __future__ import annotations

import logging
import mmap
import struct
import sys
from dataclasses import dataclass, asdict
from typing import Optional

LOG = logging.getLogger("pitbox.telemetry.sm")

# AC SM is Windows-only (Local\ namespace). On non-Windows the reader is a no-op.
IS_WINDOWS = sys.platform.startswith("win")

# Mmap names. AC nominally exposes them in the Local\ namespace, but some
# installs / Content Manager wrappers / older AC versions register them
# without the namespace prefix. We try each variant in order and remember
# the first that succeeds.
SM_PHYSICS_NAMES = ("Local\\acpmf_physics", "acpmf_physics")
SM_GRAPHICS_NAMES = ("Local\\acpmf_graphics", "acpmf_graphics")
SM_STATIC_NAMES = ("Local\\acpmf_static", "acpmf_static")
# Back-compat exports (some tests import these constants)
SM_PHYSICS = SM_PHYSICS_NAMES[0]
SM_GRAPHICS = SM_GRAPHICS_NAMES[0]
SM_STATIC = SM_STATIC_NAMES[0]

# Conservative max sizes (real structs are smaller; mmap will use file size)
SM_PHYSICS_SIZE = 2048
SM_GRAPHICS_SIZE = 2048
SM_STATIC_SIZE = 2048

# AC graphics status enum
AC_OFF, AC_REPLAY, AC_LIVE, AC_PAUSE = 0, 1, 2, 3

# AC session enum
AC_SESSION_NAMES = {
    -1: "UNKNOWN",
    0: "PRACTICE",
    1: "QUALIFY",
    2: "RACE",
    3: "HOTLAP",
    4: "TIME_ATTACK",
    5: "DRIFT",
    6: "DRAG",
}


def _wchar_to_str(buf: bytes) -> str:
    """Decode a fixed-length UTF-16LE wchar buffer, stopping at first NUL."""
    try:
        s = buf.decode("utf-16-le", errors="replace")
    except Exception:
        return ""
    nul = s.find("\x00")
    return s[:nul] if nul >= 0 else s


# ---------- Physics ----------
# We read the leading subset:
#   int packetId, float gas, brake, fuel, int gear, rpms,
#   float steerAngle, speedKmh, velocity[3], accG[3]
# = 4 + 4*3 + 4*2 + 4*2 + 4*3 + 4*3 = 52 bytes
_PHYSICS_FMT = "<i f f f i i f f 3f 3f"
_PHYSICS_SIZE = struct.calcsize(_PHYSICS_FMT)


@dataclass
class PhysicsFrame:
    packet_id: int = 0
    gas: float = 0.0
    brake: float = 0.0
    fuel: float = 0.0
    gear: int = 0          # 0=R, 1=N, 2=1st...
    rpms: int = 0
    steer_angle: float = 0.0
    speed_kmh: float = 0.0
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    velocity_z: float = 0.0


def parse_physics(buf: bytes) -> Optional[PhysicsFrame]:
    if len(buf) < _PHYSICS_SIZE:
        return None
    try:
        v = struct.unpack(_PHYSICS_FMT, buf[:_PHYSICS_SIZE])
    except struct.error as e:
        LOG.debug("physics unpack error: %s", e)
        return None
    return PhysicsFrame(
        packet_id=v[0], gas=v[1], brake=v[2], fuel=v[3], gear=v[4], rpms=v[5],
        steer_angle=v[6], speed_kmh=v[7],
        velocity_x=v[8], velocity_y=v[9], velocity_z=v[10],
    )


# ---------- Graphics ----------
# Layout (leading subset, all fields we actually use):
#   int packetId, int status, int session
#   wchar currentTime[15] (30b), wchar lastTime[15] (30b), wchar bestTime[15] (30b), wchar split[15] (30b)
#   int completedLaps, int position
#   int iCurrentTime, int iLastTime, int iBestTime
#   float sessionTimeLeft, float distanceTraveled
#   int isInPit, int currentSectorIndex, int lastSectorTime, int numberOfLaps
#   wchar tyreCompound[33] (66b)
#   float replayTimeMultiplier, float normalizedCarPosition
#   float carCoordinates[3]   (3*4 = 12b)
_GRAPHICS_HEAD = "<i i i"                    # packetId, status, session  (12)
_GRAPHICS_TIMES = "30s 30s 30s 30s"          # 4 wchar[15]                (120)  -> 132
_GRAPHICS_MID = "i i i i i f f i i i i"      # laps,pos,iCur,iLast,iBest,sessLeft,dist,inPit,sector,lastSecTime,nLaps  (44) -> 176
_GRAPHICS_TYRE = "66s"                       # wchar[33]                  (66)  -> 242
_GRAPHICS_TAIL = "f f 3f"                    # replayMult, normPos, coords[3]    (20) -> 262

_GRAPHICS_FMT = "<" + " ".join([
    "i i i",
    "30s 30s 30s 30s",
    "i i i i i f f i i i i",
    "66s",
    "f f 3f",
])
_GRAPHICS_SIZE = struct.calcsize(_GRAPHICS_FMT)


@dataclass
class GraphicsFrame:
    packet_id: int = 0
    status: int = 0
    session: int = -1
    current_time: str = ""
    last_time: str = ""
    best_time: str = ""
    split: str = ""
    completed_laps: int = 0
    position: int = 0
    i_current_time_ms: int = 0
    i_last_time_ms: int = 0
    i_best_time_ms: int = 0
    session_time_left_ms: float = 0.0
    distance_traveled: float = 0.0
    is_in_pit: int = 0
    current_sector_index: int = 0
    last_sector_time_ms: int = 0
    number_of_laps: int = 0
    tyre_compound: str = ""
    replay_time_multiplier: float = 1.0
    normalized_car_position: float = 0.0
    coord_x: float = 0.0
    coord_y: float = 0.0
    coord_z: float = 0.0
    session_name: str = "UNKNOWN"
    status_name: str = "OFF"


def parse_graphics(buf: bytes) -> Optional[GraphicsFrame]:
    if len(buf) < _GRAPHICS_SIZE:
        return None
    try:
        v = struct.unpack(_GRAPHICS_FMT, buf[:_GRAPHICS_SIZE])
    except struct.error as e:
        LOG.debug("graphics unpack error: %s", e)
        return None
    g = GraphicsFrame(
        packet_id=v[0], status=v[1], session=v[2],
        current_time=_wchar_to_str(v[3]),
        last_time=_wchar_to_str(v[4]),
        best_time=_wchar_to_str(v[5]),
        split=_wchar_to_str(v[6]),
        completed_laps=v[7], position=v[8],
        i_current_time_ms=v[9], i_last_time_ms=v[10], i_best_time_ms=v[11],
        session_time_left_ms=v[12], distance_traveled=v[13],
        is_in_pit=v[14], current_sector_index=v[15],
        last_sector_time_ms=v[16], number_of_laps=v[17],
        tyre_compound=_wchar_to_str(v[18]),
        replay_time_multiplier=v[19],
        normalized_car_position=v[20],
        coord_x=v[21], coord_y=v[22], coord_z=v[23],
    )
    g.session_name = AC_SESSION_NAMES.get(g.session, "UNKNOWN")
    g.status_name = {0: "OFF", 1: "REPLAY", 2: "LIVE", 3: "PAUSE"}.get(g.status, "OFF")
    return g


# ---------- Static ----------
# Layout (leading subset):
#   wchar smVersion[15] (30), wchar acVersion[15] (30)
#   int numberOfSessions, int numCars
#   wchar carModel[33] (66), wchar track[33] (66), wchar playerName[33] (66),
#   wchar playerSurname[33] (66), wchar playerNick[33] (66)
#   int sectorCount
_STATIC_FMT = "<30s 30s i i 66s 66s 66s 66s 66s i"
_STATIC_SIZE = struct.calcsize(_STATIC_FMT)


@dataclass
class StaticFrame:
    sm_version: str = ""
    ac_version: str = ""
    number_of_sessions: int = 0
    num_cars: int = 0
    car_model: str = ""
    track: str = ""
    player_name: str = ""
    player_surname: str = ""
    player_nick: str = ""
    sector_count: int = 3


def parse_static(buf: bytes) -> Optional[StaticFrame]:
    if len(buf) < _STATIC_SIZE:
        return None
    try:
        v = struct.unpack(_STATIC_FMT, buf[:_STATIC_SIZE])
    except struct.error as e:
        LOG.debug("static unpack error: %s", e)
        return None
    return StaticFrame(
        sm_version=_wchar_to_str(v[0]),
        ac_version=_wchar_to_str(v[1]),
        number_of_sessions=v[2],
        num_cars=v[3],
        car_model=_wchar_to_str(v[4]),
        track=_wchar_to_str(v[5]),
        player_name=_wchar_to_str(v[6]),
        player_surname=_wchar_to_str(v[7]),
        player_nick=_wchar_to_str(v[8]),
        sector_count=v[9],
    )


class SharedMemoryReader:
    """
    Lazily opens AC mmaps; returns None for any block that isn't available
    (e.g. AC not running yet). Re-tries open on each read so we recover
    automatically when AC is started after the agent.
    """

    def __init__(self) -> None:
        self._mm_physics: Optional[mmap.mmap] = None
        self._mm_graphics: Optional[mmap.mmap] = None
        self._mm_static: Optional[mmap.mmap] = None
        self._available = IS_WINDOWS
        # Names that succeeded last time, so we don't retry every variant
        # forever. None until first successful open.
        self._name_physics: Optional[str] = None
        self._name_graphics: Optional[str] = None
        self._name_static: Optional[str] = None
        # Track availability transitions so we log once on each change rather
        # than every read (which fires 15× per second).
        self._was_available: Optional[bool] = None
        if not IS_WINDOWS:
            LOG.info("AC shared memory reader inactive (non-Windows host)")

    def _open(self, current: Optional[mmap.mmap], names: tuple, size: int,
              cached_name_attr: str) -> Optional[mmap.mmap]:
        """Try each name in `names`; remember the one that worked.

        On Windows AC mmaps live under `Local\\` for normal installs but some
        wrappers expose them with no prefix. We try the cached name first
        (cheap path once AC is up) and fall through to the alternates only
        when nothing has worked yet.
        """
        if not self._available:
            return None
        if current is not None:
            return current
        # Prefer the name that worked previously, then any others in order.
        cached = getattr(self, cached_name_attr)
        order = []
        if cached:
            order.append(cached)
        for n in names:
            if n != cached:
                order.append(n)
        last_err = None
        for n in order:
            try:
                mm = mmap.mmap(-1, size, tagname=n, access=mmap.ACCESS_READ)
                if cached != n:
                    setattr(self, cached_name_attr, n)
                    LOG.info("AC SM mmap opened: name=%r size=%d", n, size)
                return mm
            except (OSError, ValueError) as e:
                last_err = e
                continue
        # Mapping doesn't exist (AC not running). Quietly retry next read.
        LOG.debug("Cannot open any of %s: %s", names, last_err)
        return None

    def read(self) -> dict:
        """
        Returns a dict { 'physics': PhysicsFrame|None, 'graphics': GraphicsFrame|None,
        'static': StaticFrame|None, 'available': bool }.
        'available' is True iff at least one of physics/graphics returned data.
        """
        if not self._available:
            return {"physics": None, "graphics": None, "static": None, "available": False}

        self._mm_physics = self._open(self._mm_physics, SM_PHYSICS_NAMES, SM_PHYSICS_SIZE, "_name_physics")
        self._mm_graphics = self._open(self._mm_graphics, SM_GRAPHICS_NAMES, SM_GRAPHICS_SIZE, "_name_graphics")
        self._mm_static = self._open(self._mm_static, SM_STATIC_NAMES, SM_STATIC_SIZE, "_name_static")

        physics = None
        graphics = None
        static = None

        if self._mm_physics is not None:
            try:
                self._mm_physics.seek(0)
                physics = parse_physics(self._mm_physics.read(_PHYSICS_SIZE))
            except (ValueError, OSError) as e:
                LOG.debug("physics read failed, dropping mmap: %s", e)
                self._safe_close("_mm_physics")

        if self._mm_graphics is not None:
            try:
                self._mm_graphics.seek(0)
                graphics = parse_graphics(self._mm_graphics.read(_GRAPHICS_SIZE))
            except (ValueError, OSError) as e:
                LOG.debug("graphics read failed, dropping mmap: %s", e)
                self._safe_close("_mm_graphics")

        if self._mm_static is not None:
            try:
                self._mm_static.seek(0)
                static = parse_static(self._mm_static.read(_STATIC_SIZE))
            except (ValueError, OSError) as e:
                LOG.debug("static read failed, dropping mmap: %s", e)
                self._safe_close("_mm_static")

        available = bool(physics or graphics)
        # One-shot transition log so operators can see in the agent log when
        # AC starts/stops without spam at the read rate.
        if available != self._was_available:
            if available:
                nick = (static.player_nick if static else "") or "(no nick yet)"
                track = (static.track if static else "") or "(no track yet)"
                car = (static.car_model if static else "") or "(no car yet)"
                LOG.info(
                    "AC SM AVAILABLE — nick=%r track=%r car=%r status=%s",
                    nick, track, car,
                    (graphics.status_name if graphics else "?"),
                )
            else:
                LOG.info("AC SM UNAVAILABLE — physics=%s graphics=%s",
                         self._name_physics or "?", self._name_graphics or "?")
            self._was_available = available
        return {"physics": physics, "graphics": graphics, "static": static, "available": available}

    def _safe_close(self, attr: str) -> None:
        mm = getattr(self, attr, None)
        if mm is not None:
            try:
                mm.close()
            except Exception:
                pass
            setattr(self, attr, None)

    def close(self) -> None:
        self._safe_close("_mm_physics")
        self._safe_close("_mm_graphics")
        self._safe_close("_mm_static")


def frame_to_payload(reader_output: dict) -> dict:
    """Convert a SharedMemoryReader.read() result to a JSON-safe dict for the wire."""
    out: dict = {"available": bool(reader_output.get("available"))}
    p = reader_output.get("physics")
    g = reader_output.get("graphics")
    s = reader_output.get("static")
    if p is not None:
        out["physics"] = asdict(p)
    if g is not None:
        out["graphics"] = asdict(g)
    if s is not None:
        out["static"] = asdict(s)
    return out
