# UniHiker K10 — Polaroid Photo Booth (viewfinder-based)
# MicroPython (k10_micropython_v0.9.8 / MicroPython 1.26). Deploy as /main.py.
#
# TAP THE SCREEN to take a photo (capture + flash + upload + return to viewfinder).
#
# Architecture: the idle state is a live camera viewfinder via screen.show_camera(cam).
# On touch, we capture a frame (works while feed is running), stop the feed briefly
# for a white flash + status text, upload, then restart the viewfinder. This avoids
# the cold-boot DMA hang that plagued the old manual-draw-based idle screen.
#
# Boot order (critical — verified 2026-06-02):
#   1. Camera init (before screen import — camera must get GDMA channels first)
#   2. Screen import (allocates I2S DMA on import, must come after camera)
#   3. WiFi connect (before screen.init — display framebuffer starves WiFi DMA)
#   4. screen.init(dir=2) + screen.stop_camera()
#   5. screen.show_camera(cam) — live viewfinder as idle state
#   6. Touch-poll loop
#
# IMPORTANT: after a soft-reset crash (Ctrl+D, brownout, or Guru Meditation),
# the auto-reboot will fail with ENOMEM because GDMA channels are not released.
# This is an ESP32-S3 limitation. Power cycle the device for a clean cold boot.
# At the event, K10s will be powered on once and run all day — not an issue.
#
# Hardware constraints baked in:
#   * camera_capture() -> 153600 bytes raw RGB565 (240x320)
#   * screen.show_camera(cam) starts the viewfinder (NOT show_camera_feed — broken)
#   * camera_capture() works while the feed is running (no conflict)
#   * Touch via FT6336 I2C @ 0x38 — independent of camera bus
#   * A/B buttons unusable (share GPIO5/11 with camera parallel bus)
#   * No asyncio (conflicts with camera)

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

# -- display constants --
SCREEN_DIR = 2       # portrait 240x320
W, H       = 240, 320

BG     = 0x0C0E1C
WHITE  = 0xFFFFFF
GOLD   = 0xC9A227
GREEN  = 0x3CDC78
RED    = 0xE63C3C
GRAY   = 0x505564

# -- camera (init FIRST, before screen import) --
_cam = Camera()
_cam.init()

from unihiker_k10 import screen  # noqa: E402

# -- touch (FT6336 on shared I2C bus) --
_i2c = k10_base.k10_i2c
_TOUCH_ADDR = 0x38


def touched() -> bool:
    """True while a finger is on the screen."""
    try:
        return (_i2c.readfrom_mem(_TOUCH_ADDR, 0x02, 1)[0] & 0x0F) > 0
    except Exception:
        return False


# -- brief status screens (shown between stop_camera and show_camera) --

def show_flash():
    screen.show_bg(color=WHITE)
    screen.show_draw()
    utime.sleep_ms(80)


def show_uploading():
    screen.show_bg(color=BG)
    screen.draw_text(text="uploading...", x=52, y=H // 2 - 10, font_size=16, color=GOLD)
    screen.show_draw()


def show_done(ok):
    screen.show_bg(color=BG)
    if ok:
        screen.draw_text(text="saved!", x=78, y=H // 2 - 10, font_size=20, color=GREEN)
    else:
        screen.draw_text(text="upload failed", x=40, y=H // 2 - 10, font_size=16, color=RED)
        screen.draw_text(text="check WiFi", x=60, y=H // 2 + 16, font_size=14, color=GRAY)
    screen.show_draw()
    utime.sleep_ms(1200)


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
    # Capture while viewfinder is still running (no conflict)
    raw = _cam.camera_capture()
    # Stop feed for brief visual feedback
    screen.stop_camera()
    show_flash()
    show_uploading()
    ok = upload(raw)
    show_done(ok)
    # Restart viewfinder
    screen.show_camera(_cam)


# -- boot --

def boot():
    """Connect WiFi BEFORE screen.init (display framebuffer starves WiFi DMA)."""
    wifi = WiFi()
    wifi.connect(ssid=SSID, psd=PASSWORD, timeout=30000)
    connected = wifi.status()

    screen.init(dir=SCREEN_DIR)
    screen.stop_camera()

    if not connected:
        # Brief error shown before viewfinder starts
        screen.show_bg(color=BG)
        screen.draw_text(text="wifi failed", x=60, y=H // 2 - 8, font_size=14, color=RED)
        screen.draw_text(text="check config", x=54, y=H // 2 + 14, font_size=12, color=GRAY)
        screen.show_draw()
        utime.sleep_ms(3000)

    # Start live viewfinder — the DMA-safe idle state
    screen.show_camera(_cam)


def run():
    armed = True
    while True:
        if touched():
            if armed:
                armed = False
                do_shoot()
        else:
            armed = True
        utime.sleep_ms(40)


boot()
run()
