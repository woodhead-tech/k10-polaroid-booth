# UniHiker K10 — Polaroid Photo Booth (headless capture + upload)
# MicroPython (k10_micropython_v0.9.8 / MicroPython 1.26). Deploy as /main.py.
#
# TAP THE SCREEN to capture + upload. No on-device display — the gallery wall
# (laptop/TV running booth.html) shows the polaroid within ~1 second of capture.
#
# Why headless: ESP32-S3 has 5 GDMA channels. Camera (parallel bus) and WiFi
# (SPI DMA) consume them all on cold boot. The screen's I2S bus cannot allocate
# DMA alongside both, so display is unavailable when camera + WiFi are active.
# This is a hardware limitation of this board, not a software bug.
#
# Boot order:
#   1. Camera init (grabs GDMA channels for parallel capture bus)
#   2. WiFi connect (grabs remaining GDMA channels for SPI)
#   3. Touch-poll loop (I2C — no DMA needed)
#
# On touch: camera_capture() → POST to server → server makes polaroid → gallery
# shows it live via WebSocket. The upload takes ~1-2s on local WiFi.

import utime
import sys
import select

# Boot guard: active stdin poll. Any byte within 3s aborts to REPL.
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

# -- camera (must init FIRST to claim GDMA channels) --
_cam = Camera()
_cam.init()

# -- paint static ready screen (works before WiFi claims DMA) --
# The LCD holds this framebuffer after deinit — stays visible while booth runs.
from unihiker_k10 import screen  # noqa: E402
screen.init(dir=2)
screen.stop_camera()
screen.show_bg(color=0x0C0E1C)
screen.draw_text(text="TAP TO SNAP", x=52, y=140, font_size=18, color=0xFFFFFF)
screen.draw_text(text=CAM_ID, x=6, y=6, font_size=14, color=0xC9A227)
screen.draw_text(text=EVENT[:20], x=6, y=296, font_size=14, color=0x505564)
screen.show_draw()
screen.deinit()
del screen

# -- WiFi (second — claims remaining GDMA channels) --
_wifi = WiFi()
_wifi.connect(ssid=SSID, psd=PASSWORD, timeout=30000)
_connected = _wifi.status()
if _connected:
    print("wifi ok")
else:
    print("wifi FAILED - uploads will fail, check config")

# -- touch (FT6336 on I2C — no DMA, always works) --
_i2c = k10_base.k10_i2c
_TOUCH_ADDR = 0x38


def touched() -> bool:
    try:
        return (_i2c.readfrom_mem(_TOUCH_ADDR, 0x02, 1)[0] & 0x0F) > 0
    except Exception:
        return False


# -- upload --

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


# -- capture flow --

def do_shoot():
    raw = _cam.camera_capture()
    ok = upload(raw)
    if ok:
        print("uploaded ok")
    else:
        print("upload failed")


# -- main loop --

print("booth ready -", CAM_ID)
armed = True
while True:
    if touched():
        if armed:
            armed = False
            do_shoot()
    else:
        armed = True
    utime.sleep_ms(40)
