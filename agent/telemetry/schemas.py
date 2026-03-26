"""
Pydantic models for telemetry and agent status messages (versioned, sequence numbers).
JSON shapes match the spec exactly for Agent->Controller and Controller->UI.
"""
from typing import Any, Optional
from pydantic import BaseModel, Field


# --- agent_status (sent on boot and every 5s) ---

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


class AgentStatusPayload(BaseModel):
    type: str = "agent_status"
    v: int = 1
    agent_id: str = ""
    device_id: str = ""
    ts_ms: int = 0
    seq: int = 0
    session: SessionInfo = Field(default_factory=SessionInfo)
    game: GameInfo = Field(default_factory=GameInfo)
    telemetry: TelemetrySourceInfo = Field(default_factory=TelemetrySourceInfo)


# --- telemetry_tick (sent at 10-20 Hz) ---

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


class TelemetryTickPayload(BaseModel):
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
