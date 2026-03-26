"""File path helpers (no dependency on agent.routes)."""


def ensure_extension(name: str, ext: str) -> str:
    """Return name with ext appended if not already ending with ext (case-insensitive). e.g. 'H-Pattern' + '.cmpreset' -> 'H-Pattern.cmpreset'."""
    n = (name or "").strip()
    if not n:
        return n
    e = ext if ext.startswith(".") else f".{ext}"
    return n if n.lower().endswith(e.lower()) else n + e
