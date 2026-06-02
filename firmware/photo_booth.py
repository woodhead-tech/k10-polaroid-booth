# UniHiker K10 — Polaroid Photo Booth (viewfinder + batched upload)
# MicroPython (k10_micropython_v0.9.8 / MicroPython 1.26). Deploy as /main.py.
#
# PRESS BUTTON A to capture. Live viewfinder on screen between shots.
# Every 5 captures, the booth pauses briefly to upload all 5 over WiFi,
# then returns to the viewfinder. This keeps capture snappy while still
# getting photos to the gallery server.
#
# Cycle:
#   1. Camera init → screen init → viewfinder (live preview)
#   2. Button A → capture frame → brief flash → back to viewfinder
#   3. After 5 captures: stop viewfinder → deinit screen → WiFi connect
#      → upload batch → WiFi disconnect → re-init screen → viewfinder
#
# GDMA budget: camera + screen fits (no WiFi). WiFi only active during upload.

import utime
import sys
import select
import gc

# Boot guard
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

import network
import k10_base
from k10_base import WiFi, Camera
from machine import Pin
from booth_config import SSID, PASSWORD, SERVER_URL, CAM_ID, EVENT

BATCH_SIZE = 5

# -- camera (must init FIRST) --
_cam = Camera()
_cam.init()

# -- screen --
from unihiker_k10 import screen  # noqa: E402

BG    = 0x0C0E1C
WHITE = 0xFFFFFF
GOLD  = 0xC9A227
GREEN = 0x3CDC78
RED   = 0xE63C3C
GRAY  = 0x505564
W, H  = 240, 320


def screen_start():
    """Init screen and start viewfinder."""
    screen.init(dir=2)
    screen.stop_camera()
    screen.show_camera(_cam)


def screen_flash():
    """Brief white flash (capture feedback)."""
    screen.stop_camera()
    screen.show_bg(color=WHITE)
    screen.show_draw()
    utime.sleep_ms(60)
    screen.show_camera(_cam)


def screen_show_uploading(n):
    """Show upload progress on screen."""
    screen.stop_camera()
    screen.show_bg(color=BG)
    screen.draw_text(text=f"uploading {n} photos", x=24, y=H // 2 - 20, font_size=16, color=GOLD)
    screen.draw_text(text="please wait...", x=54, y=H // 2 + 10, font_size=14, color=GRAY)
    screen.show_draw()


def screen_show_done(ok_count, total):
    """Show upload result."""
    screen.show_bg(color=BG)
    if ok_count == total:
        screen.draw_text(text=f"{ok_count} uploaded!", x=60, y=H // 2 - 10, font_size=18, color=GREEN)
    else:
        screen.draw_text(text=f"{ok_count}/{total} uploaded", x=40, y=H // 2 - 10, font_size=16, color=RED)
    screen.show_draw()
    utime.sleep_ms(1000)


def screen_stop():
    """Deinit screen to free DMA for WiFi."""
    screen.deinit()


# -- button --
_btn_a = Pin(5, Pin.IN, Pin.PULL_UP)


# -- upload batch --

def _post_with_timeout(url, data, headers, timeout_ms=10000):
    """POST with a real socket timeout. urequests doesn't support timeout."""
    import usocket
    # Parse URL
    proto, _, host_port, path = url.split("/", 3)
    path = "/" + path
    if ":" in host_port:
        host, port = host_port.split(":")
        port = int(port)
    else:
        host, port = host_port, 80

    addr = usocket.getaddrinfo(host, port)[0][-1]
    sock = usocket.socket()
    sock.settimeout(timeout_ms / 1000)
    sock.connect(addr)

    # Build HTTP request
    content_len = len(data)
    req = f"POST {path} HTTP/1.0\r\nHost: {host}\r\nContent-Length: {content_len}\r\n"
    for k, v in headers.items():
        req += f"{k}: {v}\r\n"
    req += "\r\n"

    sock.send(req.encode())
    # Send body in chunks (153600 bytes is too large for one send)
    mv = memoryview(data)
    sent = 0
    while sent < content_len:
        chunk = mv[sent:sent + 4096]
        sock.send(chunk)
        sent += len(chunk)

    # Read response status line
    resp = b""
    while b"\r\n" not in resp:
        resp += sock.recv(128)
    status = int(resp.split(b" ")[1])
    sock.close()
    return status


def upload_batch(frames):
    """Connect WiFi, upload all frames, disconnect. Returns success count."""
    if not HAS_HTTP or not frames:
        return 0

    ok_count = 0
    try:
        wifi = WiFi()
        wifi.connect(ssid=SSID, psd=PASSWORD, timeout=15000)
        if not wifi.status():
            print("wifi failed")
            return 0

        for raw in frames:
            try:
                status = _post_with_timeout(
                    SERVER_URL + "/upload",
                    raw,
                    {
                        "Content-Type": "image/x-rgb565",
                        "X-Width": "240",
                        "X-Height": "320",
                        "X-Cam-Id": CAM_ID,
                        "X-Event": EVENT,
                    },
                    timeout_ms=10000,
                )
                if status == 200:
                    ok_count += 1
            except Exception as e:
                print("upload err:", e)
    finally:
        try:
            wlan = network.WLAN(network.STA_IF)
            wlan.disconnect()
            wlan.active(False)
        except Exception:
            pass
        utime.sleep_ms(300)

    return ok_count


# -- main --

screen_start()
print("booth ready -", CAM_ID)

buffer = []
armed = True

while True:
    if _btn_a.value() == 1:
        if armed:
            armed = False
            # Capture (works while viewfinder running)
            raw = _cam.camera_capture()
            buffer.append(raw)
            screen_flash()
            print(f"captured ({len(buffer)}/{BATCH_SIZE})")

            # Upload batch when full
            if len(buffer) >= BATCH_SIZE:
                screen_show_uploading(len(buffer))
                screen_stop()
                gc.collect()
                try:
                    ok = upload_batch(buffer)
                    print(f"uploaded {ok}/{len(buffer)}")
                except Exception as e:
                    print("batch error:", e)
                    ok = 0
                total = len(buffer)
                buffer.clear()
                gc.collect()
                # Always re-init screen + viewfinder
                screen_start()
                screen_show_done(ok, total)
                utime.sleep_ms(800)
                screen.show_camera(_cam)
    else:
        armed = True
    utime.sleep_ms(40)
