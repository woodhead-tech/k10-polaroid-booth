# UniHiker K10 — Polaroid Photo Booth
# MicroPython — deploy via upload_booth.sh
#
# Controls:
#   Short press (<600ms) — instant capture
#   Long press  (≥600ms) — 3-2-1 countdown then capture
#
# Before deploying: verify camera and button APIs in REPL:
#   from k10_base import *; print(dir(k10_base))
#   cam = Camera(); print(dir(cam))

import utime
import uasyncio as asyncio

try:
    import urequests as requests
    HAS_HTTP = True
except ImportError:
    HAS_HTTP = False

from k10_base import WiFi, Button, Camera
from unihiker_k10 import screen
from booth_config import SSID, PASSWORD, SERVER_URL, CAM_ID, EVENT

# ── display constants ─────────────────────────────────────────────────────────
SCREEN_DIR = 2       # portrait 240×320
W, H       = 240, 320

BG     = 0x0C0E1C
WHITE  = 0xFFFFFF
GOLD   = 0xC9A227
GREEN  = 0x3CDC78
RED    = 0xE63C3C
GRAY   = 0x505564
DGRAY  = 0x1E2130

LONG_PRESS_MS = 600  # threshold for countdown vs instant


# ── drawing helpers ───────────────────────────────────────────────────────────

def _r(x, y, w, h, c):
    screen.draw_rect(x=x, y=y, w=w, h=h, bcolor=c, fcolor=c)

def _t(text, x, y, sz, c):
    screen.draw_text(text=text, x=x, y=y, font_size=sz, color=c)


# ── screens ───────────────────────────────────────────────────────────────────

def show_ready():
    """Main standby screen."""
    screen.show_bg(color=BG)
    _r(0, 0, W, 28, DGRAY)
    _t(CAM_ID, 6, 6, 14, GOLD)
    _t(EVENT[:20], W - 6 - len(EVENT[:20]) * 7, 6, 14, GRAY)

    # Center: camera icon (simple box representation)
    _r(80, 100, 80, 60, DGRAY)       # body
    _r(104, 90, 32, 14, DGRAY)       # viewfinder bump
    _r(96, 108, 48, 44, BG)          # lens surround
    _r(108, 120, 24, 20, GRAY)       # lens glass

    _t("READY", 84, 180, 20, WHITE)
    _t("short = instant", 40, 220, 12, GRAY)
    _t("hold  = 3s delay", 40, 238, 12, GRAY)
    screen.show_draw()


def show_countdown(n):
    """Display countdown digit."""
    screen.show_bg(color=BG)
    _r(0, 0, W, 28, DGRAY)
    _t(CAM_ID, 6, 6, 14, GOLD)
    color = RED if n == 1 else GOLD if n == 2 else WHITE
    # Large countdown number
    _t(str(n), W // 2 - 18, H // 2 - 40, 72, color)
    _t("SMILE!", 70, H // 2 + 50, 18, GRAY)
    screen.show_draw()


def show_flash():
    """White flash frame on capture."""
    screen.show_bg(color=WHITE)
    screen.show_draw()
    utime.sleep_ms(80)


def show_uploading():
    screen.show_bg(color=BG)
    _r(0, 0, W, 28, DGRAY)
    _t(CAM_ID, 6, 6, 14, GOLD)
    _t("uploading...", 52, H // 2 - 10, 16, GOLD)
    screen.show_draw()


def show_done(ok):
    screen.show_bg(color=BG)
    _r(0, 0, W, 28, DGRAY)
    _t(CAM_ID, 6, 6, 14, GOLD)
    if ok:
        _t("saved!", 78, H // 2 - 10, 20, GREEN)
    else:
        _t("upload failed", 40, H // 2 - 10, 16, RED)
        _t("check WiFi", 60, H // 2 + 16, 14, GRAY)
    screen.show_draw()
    utime.sleep_ms(1200)


# ── camera ────────────────────────────────────────────────────────────────────

# Camera API is not yet verified on this hardware. The code below tries each
# known pattern in order and remembers which one worked. On first successful
# capture the working method is printed to serial for future reference.
# To inspect manually in REPL: from k10_base import Camera; print(dir(Camera()))

_cam = Camera()
_cam_method = None   # set on first successful capture


def camera_capture() -> bytes:
    """Return raw JPEG bytes, auto-discovering the capture API on first call."""
    global _cam_method

    if _cam_method == 'capture':
        return _cam.capture()
    if _cam_method == 'snapshot':
        _cam.snapshot('/booth_snap.jpg')
        with open('/booth_snap.jpg', 'rb') as f:
            return f.read()

    # First call: probe available methods
    methods = dir(_cam)
    print("Camera methods:", methods)

    if 'capture' in methods:
        try:
            data = _cam.capture()
            if data and len(data) > 100:
                _cam_method = 'capture'
                print("Camera API: cam.capture() ✓")
                return data
        except Exception as e:
            print("capture() failed:", e)

    if 'snapshot' in methods:
        try:
            _cam.snapshot('/booth_snap.jpg')
            with open('/booth_snap.jpg', 'rb') as f:
                data = f.read()
            if data and len(data) > 100:
                _cam_method = 'snapshot'
                print("Camera API: cam.snapshot() ✓")
                return data
        except Exception as e:
            print("snapshot() failed:", e)

    # Last resort: dump all available attrs to serial for debugging
    print("ERROR: no working camera method found. dir(cam):", methods)
    raise RuntimeError("camera API unknown — check serial output for dir(cam)")


# ── upload ────────────────────────────────────────────────────────────────────

def upload(jpeg: bytes) -> bool:
    if not HAS_HTTP:
        return False
    try:
        r = requests.post(
            SERVER_URL + "/upload",
            data=jpeg,
            headers={
                "Content-Type": "image/jpeg",
                "X-Cam-Id":     CAM_ID,
                "X-Event":      EVENT,
            },
        )
        ok = r.status_code == 200
        r.close()
        return ok
    except Exception:
        return False


# ── capture flow ──────────────────────────────────────────────────────────────

async def countdown_and_shoot():
    for n in (3, 2, 1):
        show_countdown(n)
        await asyncio.sleep(1)
    await do_shoot()


async def instant_shoot():
    await do_shoot()


async def do_shoot():
    show_flash()
    jpeg = camera_capture()
    show_uploading()
    ok = upload(jpeg)
    show_done(ok)
    show_ready()


# ── button task ───────────────────────────────────────────────────────────────

# NOTE: Verify button API before first deploy.
# In REPL: from k10_base import Button; b = Button(); print(dir(b))
# Common: b.is_pressed() → bool
# Some K10 builds expose Button(pin) or ButtonA/ButtonB.

_btn = Button()
_shooting = False


async def task_button():
    global _shooting
    btn_down = None
    while True:
        pressed = _btn.is_pressed()
        if pressed and btn_down is None:
            btn_down = utime.ticks_ms()
        elif not pressed and btn_down is not None:
            held = utime.ticks_diff(utime.ticks_ms(), btn_down)
            btn_down = None
            if not _shooting:
                _shooting = True
                if held >= LONG_PRESS_MS:
                    await countdown_and_shoot()
                else:
                    await instant_shoot()
                _shooting = False
        await asyncio.sleep_ms(20)


# ── boot ──────────────────────────────────────────────────────────────────────

async def main():
    screen.init(dir=SCREEN_DIR)
    screen.show_bg(color=BG)
    _t("connecting...", 54, H // 2 - 8, 14, GRAY)
    screen.show_draw()

    wifi = WiFi()
    wifi.connect(ssid=SSID, psd=PASSWORD, timeout=30000)

    if wifi.status():
        screen.show_bg(color=BG)
        _t("wifi ok", 78, H // 2 - 8, 14, GREEN)
        screen.show_draw()
        utime.sleep_ms(600)
    else:
        screen.show_bg(color=BG)
        _t("wifi failed", 60, H // 2 - 8, 14, RED)
        _t("check config", 54, H // 2 + 14, 12, GRAY)
        screen.show_draw()
        utime.sleep(4)

    show_ready()
    await task_button()


asyncio.run(main())
