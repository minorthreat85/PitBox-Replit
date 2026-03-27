"""PitBox system tray launcher.

Puts a PitBox icon in the Windows system tray. Double-click or choose
'Open PitBox' to launch the UI in a dedicated app window (no address bar,
no port numbers visible). Requires pystray and Pillow.
"""

import os
import subprocess
import sys
import webbrowser

import pystray
from PIL import Image, ImageDraw, ImageFont

PITBOX_URL = "http://pitbox:9630/"
APP_NAME = "PitBox"

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _find_browser():
    for path in CHROME_PATHS + EDGE_PATHS:
        if os.path.isfile(path):
            return path
    return None


def _resource_path(filename: str) -> str:
    """Return absolute path to a bundled resource (works for PyInstaller exe and source)."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    here = os.path.dirname(os.path.abspath(__file__))
    # running from source: look in assets/ relative to repo root
    candidate = os.path.join(here, "..", "assets", filename)
    if os.path.isfile(candidate):
        return os.path.normpath(candidate)
    return os.path.join(here, filename)


def _load_icon_image():
    """Load pitbox.ico from bundled resources, fall back to generated icon."""
    ico_path = _resource_path("pitbox.ico")
    if os.path.isfile(ico_path):
        try:
            img = Image.open(ico_path)
            img = img.convert("RGBA")
            if img.size != (64, 64):
                img = img.resize((64, 64), Image.LANCZOS)
            return img
        except Exception:
            pass
    return _generate_icon(64)


def _generate_icon(size=64):
    """Fallback: generate a simple PitBox icon programmatically."""
    import math
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, size // 16)
    d.ellipse([pad, pad, size - pad - 1, size - pad - 1], fill=(22, 22, 30, 255))
    d.ellipse([pad, pad, size - pad - 1, size - pad - 1],
              outline=(0, 210, 100, 200), width=max(1, size // 24))
    try:
        font = ImageFont.truetype("arialbd.ttf", size // 3)
    except Exception:
        try:
            font = ImageFont.truetype("arial.ttf", size // 3)
        except Exception:
            font = ImageFont.load_default()
    d.text((size // 2, size // 2), "PB", fill=(255, 255, 255, 255), font=font, anchor="mm")
    return img


def open_pitbox_window():
    browser = _find_browser()
    if browser:
        try:
            subprocess.Popen(
                [browser, f"--app={PITBOX_URL}", "--window-size=1280,800"],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            return
        except Exception:
            pass
    webbrowser.open(PITBOX_URL)


def on_open(icon, item):
    open_pitbox_window()


def on_exit(icon, item):
    icon.stop()


def main():
    image = _load_icon_image()
    menu = pystray.Menu(
        pystray.MenuItem(APP_NAME, on_open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open PitBox", on_open),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", on_exit),
    )
    icon = pystray.Icon(APP_NAME, image, APP_NAME, menu)
    open_pitbox_window()
    icon.run()


if __name__ == "__main__":
    main()
