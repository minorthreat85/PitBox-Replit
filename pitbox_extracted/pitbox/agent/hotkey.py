"""
Send hotkeys (Ctrl+G, Ctrl+P) to Assetto Corsa for employee control.
Windows: uses ctypes + user32 keybd_event. Other platforms: no-op.
"""
import logging
import sys

logger = logging.getLogger(__name__)

# In-memory state for AUTO/MANUAL (toggles when toggle_manual is sent)
_control_mode: str = "AUTO"


def get_control_mode() -> str:
    """Return current control mode for status (AUTO or MANUAL)."""
    return _control_mode


def send_back_to_pits() -> bool:
    """Send Ctrl+P to foreground window. Returns True if sent."""
    global _control_mode
    if sys.platform != "win32":
        logger.warning("Hotkey only supported on Windows")
        return False
    try:
        import ctypes

        VK_CONTROL = 0x11
        VK_P = 0x50
        KEYEVENTF_KEYUP = 0x0002

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.keybd_event(VK_CONTROL, 0, 0, 0)  # Ctrl down
        user32.keybd_event(VK_P, 0, 0, 0)  # P down
        user32.keybd_event(VK_P, 0, KEYEVENTF_KEYUP, 0)  # P up
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)  # Ctrl up
        return True
    except Exception as e:
        logger.warning("send_back_to_pits failed: %s", e)
        return False


def send_toggle_manual() -> bool:
    """Send Ctrl+G to foreground window and flip local mode. Returns True if sent."""
    global _control_mode
    if sys.platform != "win32":
        logger.warning("Hotkey only supported on Windows")
        return False
    try:
        import ctypes

        VK_CONTROL = 0x11
        VK_G = 0x47
        KEYEVENTF_KEYUP = 0x0002

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_G, 0, 0, 0)
        user32.keybd_event(VK_G, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        _control_mode = "MANUAL" if _control_mode == "AUTO" else "AUTO"
        return True
    except Exception as e:
        logger.warning("send_toggle_manual failed: %s", e)
        return False
