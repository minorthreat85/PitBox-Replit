"""
Pydantic models for Controller telemetry ingest and timing_snapshot output.
Accepts agent_status and telemetry_tick; returns timing_snapshot (versioned).
"""
from typing import Optional
from pydantic import BaseModel, Field


# --- Ingest: same shape as agent sends (we accept and store) ---

class SessionInfo(BaseModel):
    mode: str = ""
    server_addr: str = ""
    server_name: str = ""
    car_model: str = ""
    skin: str = ""
    driver_name: str = ""


class GameInfo(BaseModel):
    running: bool = False
    pid: Optional[int] = None
    focused: Optional[bool] = None


class TelemetrySourceInfo(BaseModel):
    source: str = "shared_memory"
    rate_hz: float = 0.0
    ok: bool = False
    last_ok_ms: Optional[int] = None


class AgentStatusBody(BaseModel):
    type: str = "agent_status"
    v: int = 1
    agent_id: str = ""
    device_id: str = ""
    ts_ms: int = 0
    seq: int = 0
    session: SessionInfo = Field(default_factory=SessionInfo)
    game: GameInfo = Field(default_factory=GameInfo)
    telemetry: TelemetrySourceInfo = Field(default_factory=TelemetrySourceInfo)


class CarInfo(BaseModel):
    car_id: int = 0
    driver_name: str = ""
    car_model: str = ""


class TimingInfo(BaseModel):
    lap: int = 0
    lap_time_ms: Optional[int] = None
    best_lap_ms: Optional[int] = None
    last_lap_ms: Optional[int] = None
    sector: int = 0
    sector_time_ms: Optional[int] = None


class WorldPos(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class TrackInfo(BaseModel):
    track_id: str = ""
    layout: str = ""
    normalized_pos: float = 0.0
    world: WorldPos = Field(default_factory=WorldPos)
    speed_kmh: float = 0.0


class CarStateInfo(BaseModel):
    gear: int = 0
    rpm: int = 0
    throttle: float = 0.0
    brake: float = 0.0
    in_pit: bool = False


class TelemetryTickBody(BaseModel):
    type: str = "telemetry_tick"
    v: int = 1
    agent_id: str = ""
    device_id: str = ""
    ts_ms: int = 0
    seq: int = 0
    session_key: str = ""
    car: CarInfo = Field(default_factory=CarInfo)
    timing: TimingInfo = Field(default_factory=TimingInfo)
    track: TrackInfo = Field(default_factory=TrackInfo)
    car_state: CarStateInfo = Field(default_factory=CarStateInfo)


# --- Output: timing_snapshot (fused from all agents) ---

class ServerInfo(BaseModel):
    name: str = ""
    addr: str = ""
    phase: str = ""
    time_left_ms: Optional[int] = None


class TrackSnapshot(BaseModel):
    track_id: str = ""
    layout: str = ""


class LiveInfo(BaseModel):
    normalized_pos: float = 0.0
    speed_kmh: float = 0.0
    source: str = ""
    stale_ms: Optional[int] = None


class CarSnapshot(BaseModel):
    pos: int = 0
    driver: str = ""
    car_model: str = ""
    best_lap_ms: Optional[int] = None
    last_lap_ms: Optional[int] = None
    lap: int = 0
    sector: int = 0
    sector_time_ms: Optional[int] = None
    gap_ms: Optional[int] = None
    pit: bool = False
    live: LiveInfo = Field(default_factory=LiveInfo)


class TimingSnapshotBody(BaseModel):
    type: str = "timing_snapshot"
    v: int = 1
    ts_ms: int = 0
    server: ServerInfo = Field(default_factory=ServerInfo)
    track: TrackSnapshot = Field(default_factory=TrackSnapshot)
    cars: list[CarSnapshot] = Field(default_factory=list)
