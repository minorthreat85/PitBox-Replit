"""Synthetic-buffer tests for the AC shared-memory parsers."""
import struct
from unittest.mock import patch

from agent.telemetry import sm_reader
from agent.telemetry.sm_reader import (
    parse_physics, parse_graphics, parse_static,
    _PHYSICS_FMT, _GRAPHICS_FMT, _STATIC_FMT,
    frame_to_payload,
    SharedMemoryReader,
    _open_existing_mapping,
)


def _wstr(s: str, length: int) -> bytes:
    """Encode str as fixed-length UTF-16LE buffer (length = wchar count)."""
    raw = s.encode("utf-16-le")
    target_bytes = length * 2
    if len(raw) >= target_bytes:
        return raw[:target_bytes]
    return raw + b"\x00" * (target_bytes - len(raw))


def test_physics_roundtrip():
    buf = struct.pack(
        _PHYSICS_FMT,
        42,           # packetId
        0.75, 0.25, 38.5,  # gas, brake, fuel
        4, 7250,           # gear, rpms
        -0.1, 187.4,       # steerAngle, speedKmh
        50.0, 0.0, 30.0,   # velocity
        0.1, -0.2, 0.0,    # accG
    )
    p = parse_physics(buf)
    assert p is not None
    assert p.packet_id == 42
    assert abs(p.speed_kmh - 187.4) < 0.01
    assert p.gear == 4
    assert p.rpms == 7250
    assert abs(p.gas - 0.75) < 1e-6


def test_graphics_roundtrip():
    buf = struct.pack(
        _GRAPHICS_FMT,
        99, 2, 2,  # packetId, status=LIVE, session=RACE
        _wstr("1:23.456", 15),
        _wstr("1:24.000", 15),
        _wstr("1:22.111", 15),
        _wstr("+1.234", 15),
        12, 3,                # completedLaps, position
        83456, 84000, 82111,  # iCurrent/Last/Best ms
        1234.5, 4567.8,       # sessionTimeLeft, distanceTraveled
        0, 1, 27000, 30,      # isInPit, currentSectorIndex, lastSectorTime, numberOfLaps
        _wstr("Soft", 33),    # tyreCompound
        1.0, 0.482,           # replayMult, normalizedCarPosition
        100.0, 1.5, -200.0,   # carCoordinates
    )
    g = parse_graphics(buf)
    assert g is not None
    assert g.position == 3
    assert g.completed_laps == 12
    assert g.i_best_time_ms == 82111
    assert g.tyre_compound == "Soft"
    assert g.session_name == "RACE"
    assert g.status_name == "LIVE"
    assert abs(g.normalized_car_position - 0.482) < 1e-5
    assert g.is_in_pit == 0


def test_static_roundtrip():
    buf = struct.pack(
        _STATIC_FMT,
        _wstr("1.7", 15),
        _wstr("1.16.4", 15),
        3, 16,
        _wstr("ks_porsche_911_gt3_r_2016", 33),
        _wstr("spa", 33),
        _wstr("Lewis", 33),
        _wstr("Hamilton", 33),
        _wstr("LH44", 33),
        3,
    )
    s = parse_static(buf)
    assert s is not None
    assert s.car_model == "ks_porsche_911_gt3_r_2016"
    assert s.track == "spa"
    assert s.player_nick == "LH44"
    assert s.sector_count == 3
    assert s.num_cars == 16


def test_frame_to_payload_includes_present_blocks_only():
    pbuf = struct.pack(_PHYSICS_FMT, 1, 0,0,0, 0,0, 0,0, 0,0,0, 0,0,0)
    p = parse_physics(pbuf)
    out = frame_to_payload({"physics": p, "graphics": None, "static": None, "available": True})
    assert out["available"] is True
    assert "physics" in out
    assert "graphics" not in out
    assert "static" not in out


def test_truncated_buffers_return_none():
    assert parse_physics(b"") is None
    assert parse_graphics(b"\x00" * 16) is None
    assert parse_static(b"\x00" * 16) is None


# ---- Regression: must not create AC's named mappings -----------------------
# v1.5.x bug: passing fileno=-1 with tagname= to mmap.mmap on Windows CREATES
# a 2048-byte read-only anonymous mapping when the name doesn't already exist,
# which corrupts CSP's writeStatic on session load and crashes AC. We now use
# OpenFileMappingW which can ONLY attach to mappings that already exist.

def test_open_returns_none_when_mapping_does_not_exist(monkeypatch):
    """If OpenFileMappingW returns NULL (AC not running), we must return None
    and MUST NOT call MapViewOfFile or fall back to anything that creates a
    new mapping."""
    calls = {"open": 0, "map": 0}

    def fake_open(access, inherit, name):
        calls["open"] += 1
        return 0  # NULL handle = ERROR_FILE_NOT_FOUND, mapping doesn't exist

    def fake_map(*a, **kw):
        calls["map"] += 1
        return 0

    monkeypatch.setattr(sm_reader, "IS_WINDOWS", True)
    monkeypatch.setattr(sm_reader, "_OpenFileMappingW", fake_open)
    monkeypatch.setattr(sm_reader, "_MapViewOfFile", fake_map)

    view = _open_existing_mapping("Local\\acpmf_physics", 2048)
    assert view is None
    assert calls["open"] == 1
    assert calls["map"] == 0, "MapViewOfFile must not be called when OpenFileMapping returned NULL"


def test_reader_read_when_ac_not_running_returns_unavailable(monkeypatch):
    """Full read() path with no AC: never creates mappings, returns clean
    available=False payload, no exceptions."""
    monkeypatch.setattr(sm_reader, "IS_WINDOWS", True)
    monkeypatch.setattr(sm_reader, "_OpenFileMappingW", lambda *a, **kw: 0)
    # If anything tries to map, blow up loudly so the test fails:
    def explode(*a, **kw):
        raise AssertionError("MapViewOfFile must not be called when no AC mapping exists")
    monkeypatch.setattr(sm_reader, "_MapViewOfFile", explode)

    r = SharedMemoryReader()
    out = r.read()
    assert out["available"] is False
    assert out["physics"] is None
    assert out["graphics"] is None
    assert out["static"] is None


def test_open_closes_handle_when_mapview_fails(monkeypatch):
    """If OpenFileMapping succeeds but MapViewOfFile fails (e.g. mapping was
    racily destroyed), we MUST CloseHandle exactly once and return None --
    otherwise we'd leak a kernel handle every retry."""
    closed = []

    monkeypatch.setattr(sm_reader, "IS_WINDOWS", True)
    monkeypatch.setattr(sm_reader, "_OpenFileMappingW", lambda *a, **kw: 12345)
    monkeypatch.setattr(sm_reader, "_MapViewOfFile", lambda *a, **kw: 0)
    monkeypatch.setattr(sm_reader, "_CloseHandle", lambda h: (closed.append(h) or True))

    view = _open_existing_mapping("Local\\acpmf_physics", 2048)
    assert view is None
    assert closed == [12345], "CloseHandle must be called exactly once on MapViewOfFile failure"


def test_shared_view_close_is_idempotent(monkeypatch):
    """Double-close must not crash and must call Unmap/Close at most once each."""
    unmapped, closed = [], []
    monkeypatch.setattr(sm_reader, "_UnmapViewOfFile", lambda p: (unmapped.append(int(p.value or 0)) or True))
    monkeypatch.setattr(sm_reader, "_CloseHandle", lambda h: (closed.append(h) or True))

    view = sm_reader._SharedView(handle=999, addr=0xDEAD, size=2048, name="x")
    view.close()
    view.close()  # must be a no-op
    assert unmapped == [0xDEAD]
    assert closed == [999]
    assert view.handle == 0 and view.addr == 0


def test_module_does_not_use_mmap_create_idiom():
    """Belt-and-braces: source must not contain `mmap.mmap(-1` (the call that
    creates a fresh page-file-backed mapping on Windows). Comments referencing
    the old bug are fine; live calls are not."""
    import inspect, re
    src = inspect.getsource(sm_reader)
    # Strip comments and docstrings to a first approximation: drop any line
    # whose first non-whitespace char is `#`, then check for the literal call.
    code_lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    # Remove triple-quoted blocks (rough but sufficient here).
    code = re.sub(r'"""[\s\S]*?"""', "", code)
    assert "mmap.mmap(-1" not in code, "regression: mmap.mmap(-1, ...) creates AC mappings and crashes CSP"
