"""
Configuration loading and validation for PitBox Controller (simplified).
"""
import json
import logging
import os
import socket
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator

from pitbox_common.ports import (
    CONTROLLER_HTTP_PORT,
    AGENT_PORT_START,
    AGENT_PORT_END,
    ENROLLMENT_UDP_PORT,
    DISCOVERY_UDP_PORT,
)

logger = logging.getLogger(__name__)

# Ports that controller must never use for ui_port (agent range, discovery, enrollment, legacy).
_RESERVED_UI_PORTS = frozenset([
    9600,  # Legacy agent port; avoid collision.
    ENROLLMENT_UDP_PORT,
    DISCOVERY_UDP_PORT,
] + list(range(AGENT_PORT_START, AGENT_PORT_END + 1)))


def validate_ui_port(port: int | None) -> int:
    """
    Return a safe ui_port. Invalid or reserved values are replaced with CONTROLLER_HTTP_PORT.
    Safe: >= 1024, not in _RESERVED_UI_PORTS.
    """
    if port is None:
        return CONTROLLER_HTTP_PORT
    try:
        p = int(port)
    except (TypeError, ValueError):
        return CONTROLLER_HTTP_PORT
    if p < 1024 or p in _RESERVED_UI_PORTS:
        return CONTROLLER_HTTP_PORT
    return p

# AC server install root (contains acServer.exe, cfg\, presets\).
SERVER_ROOT_DEFAULT = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\assettocorsa\server"
)
# Deploy target: acServer.exe reads from here at runtime. Only written during Start/Restart (copy from preset).
ACTIVE_SERVER_CFG_DIR_DEFAULT = SERVER_ROOT_DEFAULT / "cfg"
# Source of truth per server: presets\SERVER_01\, presets\SERVER_02\, etc. UI reads/writes these; cfg\ is deploy-only.


class UpdateChannelConfig(BaseModel):
    """Update channel configuration for GitHub Releases."""
    github_owner: Optional[str] = Field(default=None, description="GitHub repo owner")
    github_repo: Optional[str] = Field(default=None, description="GitHub repo name")
    github_token: Optional[str] = Field(default=None, description="GitHub token for private repo")
    asset_controller_regex: str = Field(default=r"/PitBoxControllerSetup[^/]*\.exe$/i", description="Regex for controller installer asset")
    asset_controller_zip_regex: str = Field(default=r"/PitBoxController[^/]*\.zip$/i", description="Regex for controller update ZIP asset")
    asset_agent_regex: str = Field(default=r"/PitBoxAgentSetup[^/]*\.exe$/i", description="Regex for agent installer asset")
    cache_seconds: int = Field(default=600, description="Cache duration for release info (seconds)")
    http_timeout_seconds: int = Field(default=10, description="HTTP timeout for GitHub API")
    installer_extra_flags: Optional[str] = Field(default=None, description="Extra Inno Setup flags (e.g. /LOG)")
    min_installer_size_mb: float = Field(default=1.0, description="Minimum installer size in MB; smaller files are rejected")
    allow_prerelease: bool = Field(default=False, description="If false, prerelease versions (e.g. 0.9.3-beta.1) are ignored even if semver greater")


class AgentInfo(BaseModel):
    """Agent connection information."""
    id: str = Field(..., description="Agent unique identifier")
    host: str = Field(..., description="Agent hostname or IP")
    port: int = Field(..., description="Agent port")
    token: str = Field(..., description="Bearer token for agent")
    
    @field_validator('id')
    @classmethod
    def validate_id(cls, v: str) -> str:
        """Validate agent ID is not empty."""
        if not v or v.strip() == "":
            raise ValueError("Agent ID cannot be empty")
        return v


class ControllerConfig(BaseModel):
    """Controller configuration schema (simplified)."""
    ui_host: str = Field(default="0.0.0.0", description="UI server bind host (0.0.0.0 = listen on all interfaces, e.g. http://192.168.1.200:9630)")
    ui_port: int = Field(default=CONTROLLER_HTTP_PORT, description="UI server port")
    allow_lan_ui: bool = Field(default=False, description="Allow LAN access to UI")
    poll_interval_sec: float = Field(default=1.5, description="Agent polling interval")
    agents: list[AgentInfo] = Field(default_factory=list, description="List of agents to manage (legacy); enrolled rigs are the source of truth for UI.")
    # AC server: root (acServer.exe, cfg\, presets\); presets root = presets\ (subfolders = server profiles).
    ac_server_root: Optional[str] = Field(default=None, description="AC server root (contains acServer.exe, cfg\\, presets\\). When set, preset model is used: UI edits presets; cfg is only written on Launch.")
    ac_server_presets_root: Optional[str] = Field(default=None, description="Folder containing preset subfolders (e.g. SERVER_01, DRIFT_NIGHT). Defaults to ac_server_root/presets when not set.")
    ac_server_cfg_path: Optional[str] = Field(default=None, description="[Legacy] Single preset/cfg path. Prefer ac_server_root + ac_server_presets_root.")
    ac_servers: Optional[dict[str, str]] = Field(default=None, description="[Legacy] server_id -> path. Prefer listing presets from ac_server_presets_root.")
    ac_content_root: Optional[str] = Field(default=None, description="Path to AC install root (folder that contains 'content' and 'server'). Used for car list when ac_cars_path is not set.")
    ac_cars_path: Optional[str] = Field(default=None, description="Path directly to content\\cars (each subfolder = one car_id). When set, only this folder is scanned; parent content dir is not used.")
    server_host: Optional[str] = Field(default=None, description="Host for join when server runs on Admin PC (e.g. LAN IP). If unset, controller tries to detect LAN IP.")
    pool_server_ip: Optional[str] = Field(default=None, description="Advertised IP for dynamic pool servers. Overrides server_host if set.")
    pool_ac_server_root: Optional[str] = Field(default=None, description="Path to AC Server root for dynamic pool. Defaults to ac_server_root if unset.")
    # Global join password: same for ALL servers (venue policy). Used for race.ini [REMOTE].PASSWORD on online join.
    global_server_password: Optional[str] = Field(default=None, description="Join password for all servers. Overridable by env PITBOX_GLOBAL_SERVER_PASSWORD. If unset, preset server_cfg PASSWORD is used (can cause handshake failures).")
    # Kiosk mode: when False, customer sim display shows only race setup + results (no QR / phone pairing).
    kiosk_mode_enabled: bool = Field(default=False, description="If True, sim display shows QR and paired kiosk UI. If False, customer view is race setup + after-race results only.")
    # Sim / customer display on its own address (optional). When set, a second HTTP server binds here for /sim and API used by sim.
    sim_ui_host: Optional[str] = Field(default=None, description="Bind host for customer sim display (e.g. 192.168.1.210). If set with sim_ui_port, sim has its own IP/port.")
    sim_ui_port: Optional[int] = Field(default=None, description="Port for customer sim display. Must be valid and not in reserved range when set.")
    # Kiosk HMAC secret for QR pairing (used when kiosk_mode_enabled is True)
    kiosk_secret: Optional[str] = Field(default=None, description="Secret for signing kiosk pair tokens (HMAC). If not set, a default is used; set in production.")
    # Employee Control (mobile): password for /employee login. If unset, employee login is disabled.
    employee_password: Optional[str] = Field(default=None, description="Password for Employee Control mobile login. Set to enable /employee and hotkey API.")
    # Update channel: GitHub Releases for update checks.
    update_channel: Optional[UpdateChannelConfig] = Field(default=None, description="Update channel config for GitHub Releases")
    # Lounge branding & defaults
    lounge_name: Optional[str] = Field(default=None, description="Display name for this sim lounge (e.g. 'Fastest Lap Racing')")
    default_preset: Optional[str] = Field(default=None, description="Default server preset ID (auto-selected when joining a sim)")
    # Polling & performance tuning
    agent_poll_interval_ms: Optional[int] = Field(default=None, description="Agent polling interval in ms (500-60000). Overrides poll_interval_sec when set.")
    # AC paths (UI convenience; mirrors ac_server_root and ac_server_presets_root)
    ac_server_exe: Optional[str] = Field(default=None, description="Path to acServer.exe (convenience alias for ac_server_root)")
    ac_presets_root: Optional[str] = Field(default=None, description="Server presets root folder (convenience alias for ac_server_presets_root)")
    dev_repo_path: Optional[str] = Field(default=None, description="Path to the PitBox source repo on this machine (for in-app pull & rebuild dev updates)")
    mumble_host: Optional[str] = Field(default=None, description="Mumble gRPC server host (default: 127.0.0.1)")
    mumble_grpc_port: Optional[int] = Field(default=None, description="Mumble gRPC port (Mumble 1.4+ default: 50051)")
    mumble_token: Optional[str] = Field(default=None, description="Mumble gRPC bearer token (leave blank if not configured)")
    mumble_exe_path: Optional[str] = Field(default=None, description="Path to mumble.exe on sim PCs (used when pushing Mumble open on rigs)")

    @field_validator('agents')
    @classmethod
    def validate_agents(cls, v: list[AgentInfo]) -> list[AgentInfo]:
        """Validate agents list has unique IDs."""
        if not v:
            return v  # allow empty for default/no-config state
        
        ids = [agent.id for agent in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Agent IDs must be unique")
        
        return v

    @field_validator('sim_ui_port')
    @classmethod
    def validate_sim_ui_port(cls, v: Optional[int]) -> Optional[int]:
        """Reject reserved or invalid sim_ui_port; None is allowed."""
        if v is None:
            return None
        p = int(v)
        if p < 1024 or p in _RESERVED_UI_PORTS:
            raise ValueError(
                f"sim_ui_port must be >= 1024 and not in reserved range (e.g. {AGENT_PORT_START}-{AGENT_PORT_END})"
            )
        return p


# Global config instance and path (for UI display)
_config: Optional[ControllerConfig] = None
_config_path: Optional[Path] = None


def _validate_production_secrets(config: ControllerConfig) -> None:
    """
    Fail closed when sensitive features are enabled with missing or known-insecure secrets.
    Raises ValueError with an operator-facing message.
    """
    from pitbox_common.safe_inputs import KIOSK_INSECURE_DEFAULT_SECRET_PHRASE

    if getattr(config, "kiosk_mode_enabled", False):
        raw = (getattr(config, "kiosk_secret", None) or "").strip()
        if not raw:
            raise ValueError(
                "kiosk_mode_enabled is True but kiosk_secret is missing or empty. "
                "Set a strong, unique kiosk_secret in controller_config.json (see docs)."
            )
        if raw == KIOSK_INSECURE_DEFAULT_SECRET_PHRASE:
            raise ValueError(
                "kiosk_secret must not equal the built-in development default phrase. "
                "Set a unique kiosk_secret in controller_config.json."
            )
    ep = (getattr(config, "employee_password", None) or "").strip()
    if ep:
        if len(ep) < 5:
            raise ValueError(
                "employee_password must be at least 5 characters when set. "
                "Use a strong password or remove employee_password to allow localhost-only operator APIs."
            )
        weak = {"password", "changeme", "pitbox", "12345678", "pitbox123", "admin123"}
        if ep.lower() in weak:
            raise ValueError(
                "employee_password is too weak (commonly guessed). Choose a longer, unique password."
            )


def load_config(config_path: Path) -> ControllerConfig:
    """
    Load and validate configuration from JSON file.
    
    Args:
        config_path: Path to config JSON file
        
    Returns:
        Validated ControllerConfig instance
        
    Raises:
        FileNotFoundError: Config file not found
        ValueError: Invalid config
    """
    global _config, _config_path
    
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    logger.info("Loading config from: %s", config_path)

    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Env override for global_server_password when not set in JSON
    if "global_server_password" not in data and os.environ.get("PITBOX_GLOBAL_SERVER_PASSWORD") is not None:
        data["global_server_password"] = os.environ.get("PITBOX_GLOBAL_SERVER_PASSWORD")

    # Strict ui_port: reject reserved/invalid; never load wrong port from repo configs.
    raw_port = data.get("ui_port")
    safe_port = validate_ui_port(raw_port)
    if raw_port is not None and safe_port != raw_port:
        logger.warning(
            "ui_port %s from config is invalid or in reserved PitBox range; using %s. "
            "Reserved: 9600, %s-%s (agents), %s (discovery), %s (enrollment).",
            raw_port, safe_port, AGENT_PORT_START, AGENT_PORT_END, DISCOVERY_UDP_PORT, ENROLLMENT_UDP_PORT,
        )
    data["ui_port"] = safe_port

    config = ControllerConfig(**data)
    _validate_production_secrets(config)

    # Safety: warn on password policy
    pwd = getattr(config, "global_server_password", None)
    if pwd is not None and pwd == "":
        logger.warning(
            "global_server_password is configured but EMPTY. Joins may fail if servers require a password. "
            "Set a non-empty password in controller_config.json or PITBOX_GLOBAL_SERVER_PASSWORD."
        )
    elif pwd is None or (isinstance(pwd, str) and not pwd.strip()):
        logger.warning(
            "global_server_password is not set. race.ini [REMOTE].PASSWORD will fall back to each preset's "
            "server_cfg.ini PASSWORD (often blank or inconsistent). This can cause handshake failures. "
            "Set global_server_password in controller_config.json or PITBOX_GLOBAL_SERVER_PASSWORD for a single venue password."
        )

    _config = config
    _config_path = config_path.resolve()
    logger.info("Config loaded successfully with %d agents", len(config.agents))
    return config


def get_config() -> ControllerConfig:
    """
    Get the global config instance.
    
    Returns:
        ControllerConfig instance (default with empty agents if never loaded)
    """
    if _config is None:
        return ControllerConfig(agents=[])
    return _config


def set_default_config() -> None:
    """Set minimal default config so server can run without a config file."""
    global _config, _config_path
    _config = ControllerConfig(agents=[])
    _config_path = None
    logger.info("Using default config (no agents)")


def create_default_config(config_path: Path) -> Path:
    """Create default controller_config.json with ui_host=0.0.0.0, ui_port=CONTROLLER_HTTP_PORT, empty agents. Returns path."""
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    config = ControllerConfig(agents=[])
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(config.model_dump(), f, indent=2, ensure_ascii=False)
    logger.info("Created default controller config at %s (ui_port=%s)", path, config.ui_port)
    return path


def migrate_and_validate_legacy_config(data: dict, source_path: Path) -> dict:
    """
    Sanitize legacy config dict for one-time migration. Validates ui_port; rejects reserved/invalid.
    Returns a copy with ui_port set to a safe value. Logs warning when rejecting.
    """
    out = dict(data)
    raw_port = out.get("ui_port")
    safe_port = validate_ui_port(raw_port)
    if raw_port is not None and safe_port != raw_port:
        logger.warning(
            "Legacy config at %s: ui_port=%s rejected (reserved or invalid). Using %s. Do not adopt from legacy.",
            source_path, raw_port, safe_port,
        )
    out["ui_port"] = safe_port
    return out


def get_config_path() -> Optional[str]:
    """Return the path of the currently loaded config file, or None if using defaults."""
    if _config_path is None:
        return None
    return str(_config_path)


def save_config(config_path: Path, config: ControllerConfig) -> None:
    """
    Write controller config to a JSON file. Used by enrollment (add agent) only.
    Does not modify any agent_config.json; see ENROLLMENT.md.
    """
    global _config, _config_path
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(config.model_dump(), f, indent=2, ensure_ascii=False)
    if _config_path is not None and config_path.resolve() == _config_path.resolve():
        _config = config
    logger.info("Saved controller config to %s (%d agents)", config_path, len(config.agents))


def _uses_preset_model() -> bool:
    """True when ac_server_root is set: editing uses presets; cfg is deploy-only."""
    return bool(get_config().ac_server_root)


def get_ac_server_root() -> Path:
    """AC server root (contains acServer.exe, cfg\\, presets\\)."""
    cfg = get_config()
    if cfg.ac_server_root:
        return Path(cfg.ac_server_root)
    return SERVER_ROOT_DEFAULT


def get_ac_server_presets_root() -> Path:
    """Folder containing preset subfolders (SERVER_01, etc.). Defaults to server_root/presets."""
    cfg = get_config()
    if cfg.ac_server_presets_root:
        return Path(cfg.ac_server_presets_root).resolve()
    return get_ac_server_root() / "presets"


def get_preset_dir(server_id: str) -> Path:
    """Preset directory for a server (presets_root / server_id). For 'default' uses SERVER_01."""
    name = server_id if server_id != "default" else "SERVER_01"
    return get_ac_server_presets_root() / name


def get_live_cfg_dir() -> Path:
    """Live cfg dir (server_root/cfg). Only written on Start/Apply; acServer.exe reads from here."""
    return get_ac_server_root() / "cfg"


def list_server_preset_ids() -> list[str]:
    """List server preset names (all subdirs of presets_root) so new presets (e.g. SERVER_02) show up before config files exist."""
    presets_root = get_ac_server_presets_root()
    if not presets_root.is_dir():
        return []
    names = [p.name for p in presets_root.iterdir() if p.is_dir()]
    return sorted(names)


def get_ac_server_cfg_dir(server_id: str = "default") -> Optional[Path]:
    """
    Directory used for reading/writing server_cfg.ini and entry_list.ini.
    - When ac_server_root is set (preset model): returns preset dir (presets_root/server_id).
    - Legacy: ac_servers[server_id] or ac_server_cfg_path or ACTIVE_SERVER_CFG_DIR_DEFAULT for default.
    """
    cfg = get_config()
    if _uses_preset_model():
        return get_preset_dir(server_id)
    if cfg.ac_servers and server_id in cfg.ac_servers:
        return Path(cfg.ac_servers[server_id])
    if server_id == "default":
        if cfg.ac_server_cfg_path:
            return Path(cfg.ac_server_cfg_path)
        return ACTIVE_SERVER_CFG_DIR_DEFAULT
    return None


def _is_invalid_advertise_ip(ip: str) -> bool:
    """True if IP must never be advertised to agents (0.0.0.0, broadcast, etc.)."""
    if not ip or not isinstance(ip, str):
        return True
    ip = ip.strip()
    if ip in ("0.0.0.0", "255.255.255.255"):
        return True
    if ip.startswith("127."):
        return True
    parts = ip.split(".")
    if len(parts) != 4:
        return True
    try:
        if int(parts[3]) == 255:
            return True
    except ValueError:
        return True
    return False


def resolve_lan_ip() -> Optional[str]:
    """
    Resolve local LAN IPv4 for display/advertisement. Prefer outbound interface via
    UDP socket to 8.8.8.8:80 (no packets sent), else first non-loopback IPv4 from NICs.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        addr = s.getsockname()[0]
        s.close()
        if addr and not _is_invalid_advertise_ip(addr):
            return addr
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = (info[4][0] or "").strip()
            if addr and not _is_invalid_advertise_ip(addr):
                return addr
    except OSError:
        pass
    return None


def get_controller_http_url() -> str:
    """URL agents use to reach controller. Never advertises 0.0.0.0 or broadcast. For enrollment broadcast and startup message."""
    cfg = get_config()
    host = getattr(cfg, "server_host", None) and str(cfg.server_host).strip()
    if host and not _is_invalid_advertise_ip(host):
        return f"http://{host}:{cfg.ui_port}"
    ui = (cfg.ui_host or "").strip()
    if ui and not _is_invalid_advertise_ip(ui):
        return f"http://{ui}:{cfg.ui_port}"
    lan = resolve_lan_ip()
    if lan:
        return f"http://{lan}:{cfg.ui_port}"
    if ui == "0.0.0.0" or getattr(cfg, "allow_lan_ui", False):
        logger.warning(
            "Controller is binding on all interfaces but LAN IP could not be resolved. "
            "Set server_host in config to your LAN IP (e.g. 192.168.1.200) so the app opens at http://<LAN_IP>:9630"
        )
    return f"http://127.0.0.1:{cfg.ui_port}"
