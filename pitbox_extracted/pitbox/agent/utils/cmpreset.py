"""Content Manager .cmpreset -> assists.ini conversion (no dependency on agent.routes).

- Preset JSON can be nested under "assists" or "data"; we extract the flat dict first.
- Keys are matched case-insensitively so file casing does not drop values.
- Booleans map to 1/0 (True->1, False->0). No defaults; only preset values are written.
- Abs/TractionControl preserve 0 vs 2. StabilityControl/SlipSteam preserve 0.0, 100.0, 1.0.
- TyreWear 0.0 stays 0.0 (not 1). VisualDamage true stays 1 (not 0).
- SlipSteam (CM typo) -> SLIPSTREAM. FuelConsumption -> FUEL_RATE.
"""

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Content Manager .cmpreset JSON key -> assists.ini [ASSISTS] key (source of truth)
CMPRESET_TO_ASSISTS = {
    "IdealLine": "IDEAL_LINE",
    "AutoBlip": "AUTO_BLIP",
    "StabilityControl": "STABILITY_CONTROL",
    "AutoBrake": "AUTO_BRAKE",
    "AutoShifter": "AUTO_SHIFTER",
    "Abs": "ABS",
    "TractionControl": "TRACTION_CONTROL",
    "AutoClutch": "AUTO_CLUTCH",
    "VisualDamage": "VISUALDAMAGE",
    "Damage": "DAMAGE",
    "FuelConsumption": "FUEL_RATE",
    "TyreWear": "TYRE_WEAR",
    "TyreBlankets": "TYRE_BLANKETS",
    "SlipSteam": "SLIPSTREAM",
}
# Order of keys in written assists.ini
ASSISTS_INI_KEY_ORDER = [
    "IDEAL_LINE",
    "AUTO_BLIP",
    "STABILITY_CONTROL",
    "AUTO_BRAKE",
    "AUTO_SHIFTER",
    "SLIPSTREAM",
    "AUTO_CLUTCH",
    "ABS",
    "TRACTION_CONTROL",
    "VISUALDAMAGE",
    "DAMAGE",
    "TYRE_WEAR",
    "FUEL_RATE",
    "TYRE_BLANKETS",
]
_ASSISTS_INI_TO_JSON = {v: k for k, v in CMPRESET_TO_ASSISTS.items()}


def _extract_assists_data(data: dict) -> dict:
    """
    Extract flat assists dict from .cmpreset JSON.
    CM may store as root dict or under "assists" / "data". Keys can be any casing.
    """
    if not data or not isinstance(data, dict):
        return {}
    if "assists" in data and isinstance(data["assists"], dict):
        return data["assists"]
    if "data" in data and isinstance(data["data"], dict):
        return data["data"]
    return data


def _normalize_flat(flat: dict) -> dict[str, Any]:
    """
    Normalize flat preset to our PascalCase keys; case-insensitive match.
    Only includes keys that exist in the preset (no defaults). Preserves value types.
    """
    result: dict[str, Any] = {}
    flat_lower = {k.lower(): (k, v) for k, v in flat.items() if isinstance(k, str)}
    for our_key in CMPRESET_TO_ASSISTS:
        if our_key.lower() not in flat_lower:
            continue
        _, val = flat_lower[our_key.lower()]
        result[our_key] = val
    return result


def _format_ini_value(val: Any) -> str:
    """
    Format a preset value for assists.ini. No inversion, no default substitution.
    - bool True -> "1", False -> "0"
    - int/float preserved exactly (e.g. Abs=2, StabilityControl=100.0, TyreWear=0.0)
    """
    if val is None:
        return "0"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("true", "1", "yes"):
            return "1"
        if s in ("false", "0", "no"):
            return "0"
        try:
            return str(int(float(s))) if float(s) == int(float(s)) else str(float(s))
        except ValueError:
            pass
    return str(val)


def cmpreset_to_assists_ini(data: dict, preset_name: str | None = None) -> str:
    """
    Convert .cmpreset JSON to [ASSISTS] INI content.
    - Extracts assists from root or nested "assists"/"data".
    - Normalizes keys case-insensitively; only writes keys present in the preset (no defaults).
    - Booleans -> 1/0. Integers and floats preserved exactly.
    """
    raw_flat = _extract_assists_data(data)
    flat = _normalize_flat(raw_flat)
    lines = ["[ASSISTS]"]
    written: dict[str, str] = {}
    for ini_key in ASSISTS_INI_KEY_ORDER:
        json_key = _ASSISTS_INI_TO_JSON.get(ini_key)
        if json_key is None or json_key not in flat:
            continue
        val = flat[json_key]
        ini_val = _format_ini_value(val)
        lines.append(f"{ini_key}={ini_val}")
        written[ini_key] = ini_val

    if preset_name is not None:
        logger.debug(
            "[assists] preset=%s raw_keys=%s normalized=%s written_ini=%s",
            preset_name,
            list(raw_flat.keys()),
            flat,
            written,
        )
        logger.info(
            "[assists] preset=%s parsed_keys=%s written_ini=%s",
            preset_name,
            list(flat.keys()),
            written,
        )

    return "\n".join(lines) + "\n"


def parse_assists_ini(content: str) -> dict[str, str]:
    """
    Parse [ASSISTS] section from INI text. Returns dict of INI_KEY -> value string.
    """
    result: dict[str, str] = {}
    in_assists = False
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if line.upper().startswith("[ASSISTS]"):
            in_assists = True
            continue
        if line.startswith("["):
            if in_assists:
                break
            continue
        if in_assists and "=" in line:
            k, _, v = line.partition("=")
            key = k.strip().upper()
            val = v.strip()
            if key:
                result[key] = val
    return result


def validate_assists_ini_content(ini_content: str, expected_flat: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Re-read INI content and confirm it matches expected preset values.
    expected_flat: raw or normalized preset (e.g. IdealLine: True, Abs: 2); keys matched case-insensitively.
    Returns (ok, list of error messages).
    """
    errors: list[str] = []
    parsed = parse_assists_ini(ini_content)
    expected_normalized = _normalize_flat(expected_flat)
    for json_key, expected_val in expected_normalized.items():
        ini_key = CMPRESET_TO_ASSISTS.get(json_key)
        if ini_key is None:
            continue
        expected_str = _format_ini_value(expected_val)
        actual = parsed.get(ini_key)
        if actual is None:
            errors.append(f"Missing {ini_key} (from {json_key})")
        elif str(actual) != str(expected_str):
            errors.append(
                f"{ini_key}: expected {expected_str!r} got {actual!r} (from {json_key}={expected_val!r})"
            )
    for ini_key in parsed:
        if ini_key not in _ASSISTS_INI_TO_JSON:
            errors.append(f"Unexpected key in INI: {ini_key}")
    return (len(errors) == 0, errors)


def verify_assists_ini_after_write(assists_ini_path: Path, expected_flat: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Re-load the written assists.ini and compare every field to the selected preset.
    Returns (ok, list of error messages). Call after write_text; fail loudly if not ok.
    """
    try:
        content = assists_ini_path.read_text(encoding="utf-8")
    except OSError as e:
        return (False, [f"Cannot read assists.ini for verification: {e}"])
    ok, errors = validate_assists_ini_content(content, expected_flat)
    if not ok and errors:
        logger.error(
            "assists.ini verification FAILED: file=%s expected_flat_keys=%s errors=%s",
            assists_ini_path,
            list(expected_flat.keys()),
            errors,
        )
    return (ok, errors)
