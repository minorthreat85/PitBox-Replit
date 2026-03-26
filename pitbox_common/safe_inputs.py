"""
Strict validation for user-controlled names used in filesystem paths (presets, folders).
Shared by controller and agent to block path traversal and unsafe path segments.
"""
import re

# Sync with controller kiosk fallback secret phrase (kiosk.py).
KIOSK_INSECURE_DEFAULT_SECRET_PHRASE = "pitbox-kiosk-default-change-in-config"

MAX_PRESET_BASE_NAME_LENGTH = 192
_MAX_NAME_LEN = MAX_PRESET_BASE_NAME_LENGTH
# Windows reserved in filenames + control characters; also rejects path separators.
_RE_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def validate_steering_shifting_preset_basename(name: str) -> str:
    """
    Validate a steering (.ini) or shifting (.cmpreset) preset base name (no directory part).
    Allows letters, digits, spaces, common punctuation used in CM preset names.
    """
    s = (name or "").strip()
    if not s:
        raise ValueError("Preset name is required")
    if len(s) > _MAX_NAME_LEN:
        raise ValueError(f"Preset name is too long (max {_MAX_NAME_LEN} characters)")
    if ".." in s:
        raise ValueError("Invalid preset name")
    if _RE_UNSAFE.search(s):
        raise ValueError("Invalid preset name: path or reserved characters are not allowed")
    if not re.fullmatch(r"[\w .,'+\-()&]+", s):
        raise ValueError("Invalid preset name: use only letters, numbers, spaces, and safe punctuation")
    return s


def validate_ac_server_preset_folder_name(name: str) -> str:
    """
    Validate a preset directory name under ac_server_presets_root (e.g. SERVER_01).
    Rejects colons so ip:port favourite ids cannot be used as folder names.
    """
    s = validate_steering_shifting_preset_basename(name)
    if ":" in s:
        raise ValueError("Invalid preset folder name")
    return s
