"""
SHA-256 integrity helpers for PitBox update artifacts (ZIP, installer EXE).
Shared by controller, agent, and documented for standalone updaters.

Release metadata: HTML comments in GitHub release notes:

  <!-- pitbox_sha256:ExactAssetFileName.zip:64_hex_chars -->

Multiple assets = multiple comments. Filenames must match GitHub asset `name` exactly.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

_SHA256_ANNOTATION = re.compile(
    r"<!--\s*pitbox_sha256:([^:]+):([a-fA-F0-9]{64})\s*-->",
    re.IGNORECASE,
)


def parse_release_sha256_annotations(release_body: str) -> dict[str, str]:
    """
    Map exact asset filename -> lowercase hex sha256.
    """
    out: dict[str, str] = {}
    for m in _SHA256_ANNOTATION.finditer(release_body or ""):
        name = m.group(1).strip()
        hx = m.group(2).strip().lower()
        if len(hx) == 64 and name:
            out[name] = hx
    return out


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute lowercase hex SHA-256 of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_file_sha256(path: Path, expected_hex: str) -> tuple[bool, str]:
    """
    Return (True, "") if file hash matches expected_hex (64 hex chars, case-insensitive).
    On failure return (False, operator-facing message).
    """
    exp = (expected_hex or "").strip().lower()
    if len(exp) != 64 or not all(c in "0123456789abcdef" for c in exp):
        return False, "Invalid expected SHA-256 (must be 64 hexadecimal characters)."
    if not path.is_file():
        return False, f"Downloaded file missing or not a file: {path}"
    actual = sha256_file(path)
    if actual != exp:
        return (
            False,
            "Update integrity check failed: downloaded file SHA-256 does not match the release. "
            "Do not install; try again or contact support. "
            f"(expected {exp[:12]}…, got {actual[:12]}…)",
        )
    return True, ""
