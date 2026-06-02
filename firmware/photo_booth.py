# UniHiker K10 — Polaroid Photo Booth
# MicroPython (k10_micropython_v0.9.8 / MicroPython 1.26). Deploy as /main.py.
#
# TAP THE SCREEN to take a photo (3-2-1 countdown, then capture + upload).
#
# Hard-won facts baked into this file (see the /k10 skill for the full story):
#   * Camera.camera_capture() -> 153600 bytes raw RGB565 (240x320). The server
#     decodes BGR;16 and applies the polaroid frame.
#   * Screen singleton: `from unihiker_k10 import screen`. NEVER `import
#     unihiker_k10` (bare) or `k10_base.Screen()` — both hard-fault.
#   * The A/B face buttons share GPIO5/GPIO11 with the camera's parallel bus and
#     are UNUSABLE while the camera is initialized. Trigger is the touchscreen
#     (FT6336 on I2C(0) @ 0x38), which is independent of the camera. VERIFIED:
#     touch detection + camera_capture() + upload all work together.
#   * Connect WiFi BEFORE screen.init() (display framebuffer starves WiFi DMA).
#   * Do NOT use asyncio: the event loop conflicts with the camera. Plain sync loop.
#
# OPEN ISSUE (work in progress — booth does NOT yet fully run on cold boot):
#   Manual screen draws (show_bg/draw_text/show_draw) race with the camera's DMA
#   and HANG on a cold boot — the hang point varies (a draw, a sleep, a tight
#   loop), which is the signature of a DMA race. screen.stop_camera() tames this
#   when reached via `mpremote run` (soft reset) but NOT reliably on a fresh cold
#   boot. What DOES work on cold boot: camera init + screen.init + touch-poll +
#   camera_capture + upload, as long as we never manually draw to the screen.
#   NEXT: drive the UI with screen.show_camera_feed(<1 arg>) — a live viewfinder
#   that the firmware coordinates with the camera DMA — instead of manual draws,
#   so there is no concurrent manual-draw vs camera-DMA conflict. The draw-based
#   screens below (show_ready/countdown/etc.) are the source of the cold-boot hang.

import utime
import sys
import select

# Boot guard: active stdin poll. Any byte within 3s aborts to the REPL. A plain
# utime.sleep() does NOT work as a guard on USB-Serial-JTAG (it never reads stdin),
# and without a working guard a crashing main.py needs a full esptool reflash.
print("booth boot guard: press any key within 3s to abort to REPL")
_guard = select.poll()
_guard.register(sys.stdin, select.POLLIN)
if _guard.poll(3000):
    sys.exit()

try:
    import urequests as requests
    HAS_HTTP = True
except ImportError:
    HAS_HTTP = False

import k10_base
from k10_base import WiFi, Camera
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

# ── camera ──────────────────────────────────────────────────────────────────
# Init the camera FIRST, before importing the screen singleton.
_cam = Camera()
_cam.init()

from unihiker_k10 import screen  # noqa: E402 — must come after cam.init()


def camera_capture() -> bytes:
    return _cam.camera_capture()


# ── touch (FT6336 on the shared I2C bus) ──────────────────────────────────────
_i2c = k10_base.k10_i2c
_TOUCH_ADDR = 0x38


def touched() -> bool:
    """True while a finger is on the screen. Register 0x02 low nibble = touch count."""
    try:
        return (_i2c.readfrom_mem(_TOUCH_ADDR, 0x02, 1)[0] & 0x0F) > 0
    except Exception:
        return False


# ── drawing helpers ───────────────────────────────────────────────────────────

def _r(x, y, w, h, c):
    screen.draw_rect(x=x, y=y, w=w, h=h, bcolor=c, fcolor=c)

def _t(text, x, y, sz, c):
    screen.draw_text(text=text, x=x, y=y, font_size=sz, color=c)


# ── screens ───────────────────────────────────────────────────────────────────

def show_ready():
    screen.show_bg(color=BG)
    _r(0, 0, W, 28, DGRAY)
    _t(CAM_ID, 6, 6, 14, GOLD)
    _t(EVENT[:20], W - 6 - len(EVENT[:20]) * 7, 6, 14, GRAY)

    # camera icon
    _r(80, 100, 80, 60, DGRAY)
    _r(104, 90, 32, 14, DGRAY)
    _r(96, 108, 48, 44, BG)
    _r(108, 120, 24, 20, GRAY)

    _t("TAP TO SNAP", 52, 190, 18, WHITE)
    screen.show_draw()


def show_countdown(n):
    screen.show_bg(color=BG)
    _r(0, 0, W, 28, DGRAY)
    _t(CAM_ID, 6, 6, 14, GOLD)
    color = RED if n == 1 else GOLD if n == 2 else WHITE
    _t(str(n), W // 2 - 18, H // 2 - 40, 72, color)
    _t("SMILE!", 70, H // 2 + 50, 18, GRAY)
    screen.show_draw()


def show_flash():
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


# ── upload ────────────────────────────────────────────────────────────────────

def upload(raw: bytes) -> bool:
    if not HAS_HTTP:
        return False
    try:
        r = requests.post(
            SERVER_URL + "/upload",
            data=raw,
            headers={
                "Content-Type": "image/x-rgb565",
                "X-Width":      "240",
                "X-Height":     "320",
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

def do_shoot():
    for n in (3, 2, 1):
        show_countdown(n)
        utime.sleep_ms(1000)
    show_flash()
    raw = camera_capture()
    show_uploading()
    ok = upload(raw)
    show_done(ok)


# ── boot ──────────────────────────────────────────────────────────────────────

def boot():
    """Connect WiFi BEFORE screen.init() (the display framebuffer would starve the
    WiFi DMA), then init the screen and immediately stop_camera() to halt the
    preview DMA that otherwise hangs every delay/draw."""
    wifi = WiFi()
    wifi.connect(ssid=SSID, psd=PASSWORD, timeout=30000)
    connected = wifi.status()

    screen.init(dir=SCREEN_DIR)
    screen.stop_camera()

    if connected:
        screen.show_bg(color=BG)
        _t("wifi ok", 78, H // 2 - 8, 14, GREEN)
        screen.show_draw()
        utime.sleep_ms(600)
    else:
        screen.show_bg(color=BG)
        _t("wifi failed", 60, H // 2 - 8, 14, RED)
        _t("check config", 54, H // 2 + 14, 12, GRAY)
        screen.show_draw()
        utime.sleep_ms(3000)


def run():
    show_ready()
    armed = True
    while True:
        if touched():
            if armed:
                armed = False
                do_shoot()
                show_ready()
        else:
            armed = True   # require finger lift before the next shot
        utime.sleep_ms(40)


boot()
run()
