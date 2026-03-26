"""Content Manager Favourites.txt resolution and loading (no dependency on api_routes)."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_favourites_cache: Optional[tuple[float, Optional[Path], Optional[float], list[dict[str, Any]], bool, list[str]]] = None
_FAVOURITES_CACHE_TTL_SEC = 10.0
_FAVOURITES_NO_FILE_CACHE_TTL_SEC = 5.0


def _favourites_candidate_paths() -> list[tuple[Path, str]]:
    """Return (path, strategy_name) for resolution order: env, known fallback, then Users/* search."""
    candidates: list[tuple[Path, str]] = []
    env_expanded = os.path.expandvars(r"%LOCALAPPDATA%\AcTools Content Manager\Online Servers\Favourites.txt")
    if env_expanded and "%" not in env_expanded:
        candidates.append((Path(env_expanded), "LOCALAPPDATA"))
    fallback = Path(r"C:\Users\info\AppData\Local\AcTools Content Manager\Online Servers\Favourites.txt")
    candidates.append((fallback, "fallback_C:\\Users\\info"))
    try:
        users_dir = Path(r"C:\Users")
        if users_dir.is_dir():
            for p in users_dir.iterdir():
                if p.is_dir() and p.name not in (".", "..", "Public", "Default", "Default User", "All Users"):
                    fav = p / r"AppData\Local\AcTools Content Manager\Online Servers\Favourites.txt"
                    candidates.append((fav.resolve(), f"Users\\{p.name}"))
    except (OSError, PermissionError):
        pass
    return candidates


def resolve_favourites_txt_path() -> tuple[Optional[Path], bool, list[str]]:
    """
    Resolve Favourites.txt using multiple strategies. Returns (path or None, used_fallback, paths_tried).
    Strategy order: %LOCALAPPDATA%, then C:\\Users\\info\\..., then C:\\Users\\*\\...
    """
    paths_tried: list[str] = []
    for path, strategy in _favourites_candidate_paths():
        try:
            resolved = path.resolve() if path else path
            pstr = str(resolved)
            paths_tried.append(pstr)
            if resolved and resolved.is_file():
                used_fallback = strategy != "LOCALAPPDATA"
                logger.info("[favourites] resolved path=%s strategy=%s used_fallback=%s", pstr, strategy, used_fallback)
                return resolved, used_fallback, paths_tried
        except (OSError, PermissionError, RuntimeError):
            paths_tried.append(str(path) + " (error)")
            continue
    logger.info("[favourites] no Favourites.txt found paths_tried=%s", paths_tried)
    return None, False, paths_tried


def _read_favourites_txt(path: Path) -> list[dict[str, Any]]:
    """
    Read and parse Favourites.txt. Format per line: IP:PORT;SERVER NAME.
    Returns list of { server_id, name, ip, port, join_addr, source }. Dedupes by (ip, port).
    """
    result: list[dict[str, Any]] = []
    seen_addr: set[tuple[str, int]] = set()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, PermissionError) as e:
        logger.warning("[favourites] read failed path=%s e=%s", path, e)
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line or ";" not in line:
            continue
        part_addr, _, part_name = line.partition(";")
        part_addr = part_addr.strip()
        part_name = (part_name or "").strip() or part_addr
        if ":" not in part_addr:
            continue
        host, _, port_s = part_addr.rpartition(":")
        host = (host or "").strip()
        port_s = (port_s or "").strip()
        if not host or not port_s or not port_s.isdigit():
            continue
        port = int(port_s)
        if port < 1 or port > 65535:
            continue
        addr = (host, port)
        if addr in seen_addr:
            continue
        seen_addr.add(addr)
        server_id = f"{host}:{port}"
        join_addr = f"{host}:{port}"
        result.append({
            "server_id": server_id,
            "name": part_name,
            "ip": host,
            "port": port,
            "join_addr": join_addr,
            "source": "favorite",
        })
    return result


def load_favourites_servers() -> list[dict[str, Any]]:
    """
    Load Content Manager favourites from Favourites.txt (path resolved dynamically).
    Returns list of { server_id, name, ip, port, join_addr, source }. Cached 10s; invalidated on path/mtime change.
    """
    global _favourites_cache
    now = time.time()
    path, used_fallback, paths_tried = resolve_favourites_txt_path()
    if path is None:
        if _favourites_cache is not None:
            _ts, _path, _mtime, cached_list, _uf, _pt = _favourites_cache
            if _path is None and (now - _ts) <= _FAVOURITES_NO_FILE_CACHE_TTL_SEC:
                return cached_list
        _favourites_cache = (now, None, None, [], False, paths_tried)
        logger.debug("[favourites] no file cache_age=0 count=0")
        return []
    try:
        mtime = path.stat().st_mtime
    except (OSError, PermissionError):
        mtime = None
    if _favourites_cache is not None:
        _ts, _path, _mtime, cached_list, _uf, _pt = _favourites_cache
        if _path is not None and _path == path and _mtime == mtime and (now - _ts) <= _FAVOURITES_CACHE_TTL_SEC:
            logger.debug("[favourites] cache hit path=%s count=%d age=%.1fs", path, len(cached_list), now - _ts)
            return cached_list
        logger.debug("[favourites] cache invalid path_or_mtime_changed or expired")
    result = _read_favourites_txt(path)
    _favourites_cache = (now, path, mtime, result, used_fallback, paths_tried)
    logger.info("[favourites] loaded path=%s count=%d used_fallback=%s", path, len(result), used_fallback)
    return result


def get_favourites_debug_info() -> dict[str, Any]:
    """Return debug info: resolved_path, exists, mtime, count, cache_age_sec, used_fallback, search_paths_tried."""
    global _favourites_cache
    path, used_fallback, paths_tried = resolve_favourites_txt_path()
    info: dict[str, Any] = {
        "resolved_path": str(path) if path else None,
        "exists": path is not None and path.is_file() if path else False,
        "mtime": None,
        "count": 0,
        "cache_age_sec": None,
        "used_fallback": used_fallback,
        "search_paths_tried": paths_tried,
    }
    if path:
        try:
            info["mtime"] = path.stat().st_mtime
        except (OSError, PermissionError):
            pass
        if _favourites_cache is not None:
            _ts, _path, _mtime, cached_list, _uf, _pt = _favourites_cache
            if _path == path:
                info["count"] = len(cached_list)
                info["cache_age_sec"] = round(time.time() - _ts, 2)
    return info
