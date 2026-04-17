"""
Configuration loading and validation for PitBox Agent (simplified).
Tolerates UTF-8 BOM when reading; writes without BOM.
Expands %USERPROFILE% and other env vars in path strings.
"""
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pitbox_common.ports import AGENT_PORT_START, agent_port_for_sim


logger = logging.getLogger(__name__)


def _expand_path(s: str) -> str:
    """Expand %VAR% and $VAR in path strings (e.g. %USERPROFILE%)."""
    if not s:
        return s
    return os.path.expandvars(s)


def _load_json_robust(config_path: Path) -> dict:
    """
    Load JSON from file, tolerating UTF-8 BOM.
    Uses utf-8-sig to strip BOM if present.
    """
    try:
        with open(config_path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {config_path}: {e}")
        raise ValueError(f"Invalid JSON in config file: {e}") from e
    except OSError as e:
        logger.error(f"Cannot read config file {config_path}: {e}")
        raise


class PathsConfig(BaseModel):
    """File paths configuration. Supports multiple naming styles; extra keys (e.g. preset_root) ignored."""
    model_config = ConfigDict(extra="ignore")
    acs_exe: str = Field(..., description="Path to acs.exe")
    # Preset source (where .ini files live): any of these names work
    managed_steering_templates: Optional[str] = Field(None, description="Directory of .ini steering preset templates")
    savedsetups: Optional[str] = Field(None, description="Same as managed_steering_templates (preset .ini folder)")
    savedsetups_dir: Optional[str] = Field(None, description="Alias for savedsetups")
    # Directory that contains controls.ini (we write controls.ini here)
    ac_savedsetups: Optional[str] = Field(None, description="Directory containing controls.ini")
    ac_cfg: Optional[str] = Field(None, description="Same as ac_savedsetups (cfg folder)")
    ac_cfg_dir: Optional[str] = Field(None, description="Alias for ac_cfg")
    # Assists/shifting presets: Content Manager .cmpreset files (apply to assists.ini)
    cm_assists_presets: Optional[str] = Field(None, description="CM Presets\\Assists folder (.cmpreset files)")
    shifting_templates: Optional[str] = Field(None, description="Alias for cm_assists_presets (same folder)")
    cm_assists_presets_dir: Optional[str] = Field(None, description="Alias for cm_assists_presets")
    # AC session time limit (writes MAX_RUNNING_TIME in minutes)
    time_limited_test_ini: Optional[str] = Field(
        None,
        description="Path to time_limited_test.ini (AC cfg). If unset, uses default Steam AC install cfg path.",
    )

    @model_validator(mode="after")
    def expand_paths(self):
        """Expand %USERPROFILE% etc. and apply aliases (e.g. ac_cfg_dir -> ac_cfg)."""
        # Copy alias -> canonical if canonical is missing
        if not (self.ac_cfg or self.ac_savedsetups) and self.ac_cfg_dir:
            self.ac_cfg = self.ac_cfg_dir
        if not (self.savedsetups or self.managed_steering_templates) and self.savedsetups_dir:
            self.savedsetups = self.savedsetups_dir
        if not (self.cm_assists_presets or self.shifting_templates) and self.cm_assists_presets_dir:
            self.cm_assists_presets = self.cm_assists_presets_dir
        # Expand env vars in all path fields
        for name in (
            "acs_exe", "managed_steering_templates", "savedsetups", "savedsetups_dir",
            "ac_savedsetups", "ac_cfg", "ac_cfg_dir", "cm_assists_presets", "shifting_templates", "cm_assists_presets_dir",
            "time_limited_test_ini",
        ):
            v = getattr(self, name, None)
            if v and isinstance(v, str):
                setattr(self, name, _expand_path(v))
        return self


class AgentConfig(BaseModel):
    """Agent configuration schema (simplified - no presets). Unknown top-level keys are ignored."""
    model_config = ConfigDict(extra="ignore")
    agent_id: str = Field(..., description="Unique agent identifier")
    token: str = Field(..., description="Bearer token for authentication")
    listen_host: str = Field(default="0.0.0.0", description="Host to bind server. Default 0.0.0.0 for LAN reachability.")
    port: Optional[int] = Field(default=None, description="Port to bind. If unset or 9600, derived from agent_id: Sim1->9631, ..., Sim8->9638.")
    paths: PathsConfig
    controller_url: Optional[str] = Field(default=None, description="Controller base URL for heartbeat (e.g. http://192.168.1.1:9630). If set, agent sends X-Agent-Id and X-Agent-Token to /api/heartbeat.")
    auto_launch_display: bool = Field(default=False, description="If true, auto-launch Chrome/Edge in kiosk fullscreen pointing at the controller /sim page on startup.")
    display_launch_delay: float = Field(default=5.0, description="Seconds to wait after startup before launching the sim display browser.")
    mumble_server_host: str = Field(default="192.168.1.200", description="Murmur server IP/hostname for auto-connect URL.")
    mumble_server_port: int = Field(default=64738, description="Murmur server port.")
    mumble_channel: str = Field(default="Race Control", description="Mumble channel to join on auto-connect.")
    mumble_exe_path: Optional[str] = Field(default=None, description="Full path to mumble.exe. If unset, standard install paths are tried.")
    mumble_server_password: str = Field(default="fastestlap", description="Murmur server password included in the auto-connect URL.")

    @field_validator('token')
    @classmethod
    def validate_token(cls, v: str) -> str:
        """Validate token is not empty."""
        if not v or v.strip() == "":
            raise ValueError("Token cannot be empty")
        return v
    
    @field_validator('agent_id')
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        """Validate agent_id is not empty."""
        if not v or v.strip() == "":
            raise ValueError("agent_id cannot be empty")
        return v


# Global config instance and path (used so pairing/identity use same folder as agent_config.json)
_config: Optional[AgentConfig] = None
_loaded_config_path: Optional[Path] = None

# Default agent config dir when config not yet loaded (e.g. C:/PitBox/Agent/config)
AGENT_CONFIG_DIR_DEFAULT = Path("C:/PitBox/Agent/config")


def _sim_number_from_agent_id(agent_id: str) -> Optional[int]:
    """Extract sim number from agent_id (e.g. 'Sim1' -> 1, 'sim5' -> 5). Returns None if not SimN."""
    if not agent_id or not isinstance(agent_id, str):
        return None
    m = re.match(r"^sim(\d+)$", agent_id.strip().lower())
    return int(m.group(1)) if m else None


def resolve_agent_port(config: AgentConfig) -> int:
    """Effective listen port: config.port if set (and not legacy 9600), else derived from agent_id (Sim1->9631, ..., Sim8->9638)."""
    # Legacy: treat 9600 as unset so we use derived port (9631-9638)
    if config.port is not None and config.port != 9600 and 1 <= config.port <= 65535:
        return config.port
    sim = _sim_number_from_agent_id(config.agent_id)
    if sim is not None:
        try:
            return agent_port_for_sim(sim)
        except ValueError:
            pass
    return AGENT_PORT_START


def get_agent_config_dir() -> Path:
    """Directory for agent_config.json, pairing.json, identity.json. Always C:/PitBox/Agent/config unless loaded config path is inside that directory (prevents identity.json in random cwd)."""
    global _loaded_config_path
    default = AGENT_CONFIG_DIR_DEFAULT.resolve()
    if _loaded_config_path is None:
        return default
    try:
        parent = _loaded_config_path.resolve().parent
        default_resolved = default.resolve()
        # If loaded config is inside C:/PitBox/Agent/config, use its parent so we use same dir as config.
        if parent == default_resolved or str(parent).startswith(str(default_resolved) + os.sep):
            return parent
    except Exception:
        pass
    return default


def load_config(config_path: Path) -> AgentConfig:
    """
    Load and validate configuration from JSON file.
    
    Args:
        config_path: Path to config JSON file
        
    Returns:
        Validated AgentConfig instance
        
    Raises:
        FileNotFoundError: Config file not found
        ValueError: Invalid config
    """
    global _config, _loaded_config_path
    
    _loaded_config_path = Path(config_path).resolve()
    if not _loaded_config_path.exists():
        raise FileNotFoundError(f"Config file not found: {_loaded_config_path}")
    
    logger.info(f"Loading config from: {_loaded_config_path}")
    
    data = _load_json_robust(_loaded_config_path)
    # Legacy auto-heal: fix localhost and port 9600 so older installs get LAN-safe behavior.
    if data.get("listen_host") == "127.0.0.1" and not data.get("local_only"):
        data["listen_host"] = "0.0.0.0"
        logger.warning(
            "agent_config.json had listen_host 127.0.0.1; auto-healed to 0.0.0.0 for LAN reachability. "
            "Set \"local_only\": true to keep binding to localhost."
        )
    if data.get("port") == 9600:
        data["port"] = None
        logger.warning(
            "agent_config.json had legacy port 9600; auto-healed to null so port is derived from agent_id (Sim1->9631, ...)."
        )
    config = AgentConfig(**data)
    # Warn if acs_exe is missing (agent still starts; launch will fail)
    acs_exe = Path(config.paths.acs_exe)
    if not acs_exe.exists():
        logger.warning("acs.exe not found at: %s (agent will start; launch will fail)", config.paths.acs_exe)
    _config = config
    logger.info("Config loaded successfully for agent: %s", config.agent_id)
    return config


def get_config() -> AgentConfig:
    """
    Get the global config instance.
    
    Returns:
        AgentConfig instance
        
    Raises:
        RuntimeError: Config not loaded
    """
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")
    return _config


def get_preset_dir(config: AgentConfig) -> Optional[str]:
    """Preset source: e.g. %USERPROFILE%\\Documents\\Assetto Corsa\\cfg\\controllers\\savedsetups. Read-only; we never write here."""
    p = config.paths
    raw = getattr(p, "managed_steering_templates", None) or getattr(p, "savedsetups", None)
    return _expand_path(raw) if raw else None


def get_controls_ini_dir(config: AgentConfig) -> Optional[str]:
    """Target dir for controls.ini and assists.ini. Prefer ac_cfg_dir, then ac_cfg, then ac_savedsetups. Already expanded."""
    p = config.paths
    raw = getattr(p, "ac_cfg_dir", None) or getattr(p, "ac_cfg", None) or getattr(p, "ac_savedsetups", None)
    return raw if raw else None


# Default path for time_limited_test.ini (AC install cfg)
DEFAULT_TIME_LIMITED_TEST_INI = r"C:\Program Files (x86)\Steam\steamapps\common\assettocorsa\cfg\time_limited_test.ini"


def get_time_limited_test_ini_path(config: AgentConfig) -> Path:
    """Path to time_limited_test.ini. Uses paths.time_limited_test_ini if set, else default Steam AC cfg path."""
    p = getattr(config.paths, "time_limited_test_ini", None)
    if p and isinstance(p, str) and (p := p.strip()):
        return Path(p)
    return Path(DEFAULT_TIME_LIMITED_TEST_INI)


def get_ac_out_dir(config: AgentConfig) -> Optional[Path]:
    """AC out folder (e.g. Documents\\Assetto Corsa\\out) where race_out.json is written. Derived from cfg dir parent / 'out'."""
    cfg_raw = get_controls_ini_dir(config)
    if not cfg_raw:
        return None
    try:
        return Path(cfg_raw).resolve().parent / "out"
    except Exception:
        return None


def get_assists_presets_dir(config: AgentConfig) -> Optional[str]:
    """Source dir for .cmpreset files. Prefer cm_assists_presets_dir, then cm_assists_presets, then shifting_templates. Already expanded."""
    p = config.paths
    raw = getattr(p, "cm_assists_presets_dir", None) or getattr(p, "cm_assists_presets", None) or getattr(p, "shifting_templates", None)
    return raw if raw else None


def get_agent_state_path(config: AgentConfig) -> Path:
    """Path to persistent agent state (display_name etc.). Uses LOCALAPPDATA/PitBox/agent_{agent_id}_state.json."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    pitbox = Path(base) / "PitBox"
    pitbox.mkdir(parents=True, exist_ok=True)
    safe_id = (config.agent_id or "default").replace(os.path.sep, "_").strip() or "default"
    return pitbox / f"agent_{safe_id}_state.json"
