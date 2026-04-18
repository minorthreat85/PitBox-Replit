"""Synthetic-buffer tests for the AC shared-memory parsers."""
import struct

from agent.telemetry.sm_reader import (
    parse_physics, parse_graphics, parse_static,
    _PHYSICS_FMT, _GRAPHICS_FMT, _STATIC_FMT,
    frame_to_payload,
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
