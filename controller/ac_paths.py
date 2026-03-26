"""AC server root, content root, and preset/cfg path helpers (shared by API routes and server-config)."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from controller.config import (
    SERVER_ROOT_DEFAULT,
    get_ac_server_cfg_dir,
    get_ac_server_root,
    get_config,
    get_live_cfg_dir,
    get_preset_dir,
    _uses_preset_model,
)

logger = logging.getLogger(__name__)


def _server_root_from_path(path: Path) -> Path:
    """Walk up from any path under the server to find the dir that contains acServer.exe."""
    p = path.resolve()
    for _ in range(15):
        if (p / "acServer.exe").exists():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent
    return path.parent if path.name == "presets" else path.parent.parent


def _server_root() -> Path:
    """AC server root (contains acServer.exe, cfg\\, presets\\). From config or derived from legacy path."""
    if _uses_preset_model():
        return get_ac_server_root()
    cfg = get_config()
    if cfg.ac_server_cfg_path:
        from_path = _server_root_from_path(Path(cfg.ac_server_cfg_path))
        if (from_path / "acServer.exe").exists():
            return from_path
    return SERVER_ROOT_DEFAULT


def _content_root() -> Path:
    """AC install root (contains content/cars, content/tracks). Prefer ac_content_root from config."""
    cfg = get_config()
    if cfg.ac_content_root:
        p = Path(cfg.ac_content_root).resolve()
        if p.is_dir() and (p / "content" / "tracks").is_dir():
            return p
    candidate = _server_root().parent.resolve()
    if (candidate / "content" / "tracks").is_dir():
        return candidate
    default_ac = Path(r"C:\Program Files (x86)\Steam\steamapps\common\assettocorsa")
    if default_ac.is_dir() and (default_ac / "content" / "tracks").is_dir():
        logger.debug("[content_root] using default Steam path: %s", default_ac)
        return default_ac
    return candidate


def _cars_dir() -> Path:
    """Directory to scan for cars: each subfolder = one car_id."""
    cfg = get_config()
    if cfg.ac_cars_path:
        p = Path(cfg.ac_cars_path)
        if p.is_dir():
            return p
    return _content_root() / "content" / "cars"


def _tracks_dir() -> Path:
    """AC content/tracks."""
    root = _content_root()
    tracks = root / "content" / "tracks"
    if not tracks.is_dir():
        logger.warning(
            "[tracks_dir] content/tracks not found at %s (track outlines will 404). Set ac_content_root in controller config.",
            tracks,
        )
    return tracks


def _car_cache_dir(car_id: str) -> Path:
    """CM/AC cache folder for a car's preview images."""
    userprofile = os.environ.get("USERPROFILE", "")
    if not userprofile:
        return Path()
    return Path(userprofile) / "Documents" / "Assetto Corsa" / "cache" / "cars" / car_id


def _normalize_layout_for_cache(layout: Optional[str]) -> Optional[str]:
    if layout is None:
        return None
    if not (s := layout.strip()):
        return None
    if s.lower() == "default":
        return None
    if ".." in s or "/" in s or "\\" in s:
        return None
    return s


def _track_cache_dir(track_id: str, layout: Optional[str] = None) -> Path:
    """CM/AC cache folder for track preview images."""
    userprofile = os.environ.get("USERPROFILE", "")
    if not userprofile:
        return Path()
    base = Path(userprofile) / "Documents" / "Assetto Corsa" / "cache" / "tracks" / track_id
    layout_norm = _normalize_layout_for_cache(layout)
    if layout_norm:
        return base / layout_norm.strip()
    return base


def _deploy_cfg_dir() -> Path:
    """Deploy target: cfg\\. Only written during Start/Restart (copy from preset)."""
    if _uses_preset_model():
        return get_live_cfg_dir()
    return _server_root() / "cfg"


def _preset_dir_for_server(server_id: str) -> Path:
    """Preset dir for server_id (presets_root/server_id)."""
    if _uses_preset_model():
        return get_preset_dir(server_id)
    preset_name = server_id if server_id != "default" else "SERVER_01"
    return _server_root() / "presets" / preset_name


def _cfg_dir_for_server(server_id: str) -> Path:
    """Dir for server_cfg.ini and entry_list.ini."""
    cfg_dir = get_ac_server_cfg_dir(server_id)
    if cfg_dir is not None:
        return cfg_dir
    return _preset_dir_for_server(server_id)


def _server_config_paths(cfg_dir: Path) -> tuple[Path, Path]:
    return cfg_dir / "server_cfg.ini", cfg_dir / "entry_list.ini"


def _server_config_paths_for_read(cfg_dir: Path) -> tuple[Path, Path]:
    """
    Paths for reading server_cfg.ini / entry_list.ini. Prefer <preset>/cfg/ if it exists.
    """
    sc_cfg = cfg_dir / "cfg" / "server_cfg.ini"
    el_cfg = cfg_dir / "cfg" / "entry_list.ini"
    sc_root = cfg_dir / "server_cfg.ini"
    el_root = cfg_dir / "entry_list.ini"
    if sc_cfg.exists():
        return sc_cfg, el_cfg
    return sc_root, el_root
