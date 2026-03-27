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


def create_icon_image(size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([1, 1, size - 2, size - 2], fill=(18, 18, 18, 255))
    draw.ellipse([1, 1, size - 2, size - 2], outline=(0, 210, 110, 255), width=3)
    try:
        font = ImageFont.truetype("arialbd.ttf", size // 3)
    except Exception:
        try:
            font = ImageFont.truetype("arial.ttf", size // 3)
        except Exception:
            font = ImageFont.load_default()
    draw.text((size // 2, size // 2), "PB", fill=(255, 255, 255, 255), font=font, anchor="mm")
    return img


def on_open(icon, item):
    open_pitbox_window()


def on_exit(icon, item):
    icon.stop()


def main():
    image = create_icon_image(64)
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
