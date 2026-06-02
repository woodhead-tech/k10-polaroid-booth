# UniHiker K10 — Polaroid Photo Booth (viewfinder + batched upload)
# MicroPython (k10_micropython_v0.9.8 / MicroPython 1.26). Deploy as /main.py.
#
# PRESS BUTTON A to capture. Live viewfinder on screen for first batch.
# Every 5 captures: stop viewfinder → upload → soft reset for clean state.
# Each boot cycle gives a fresh viewfinder for the next batch.
#
# Why reset after upload: screen.deinit() is required to free DMA for WiFi,
# but screen.init() hangs on re-initialization (singleton state corruption).
# A machine.reset() gives a clean cold boot with viewfinder for the next batch.
# This takes ~8s (boot guard + WiFi connect + upload + reset + boot).

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
import machine
import k10_base
from k10_base import WiFi, Camera
from machine import Pin
from booth_config import SSID, PASSWORD, SERVER_URL, CAM_ID, EVENT

BATCH_SIZE = 5

# -- camera --
_cam = Camera()
_cam.init()

# -- screen + viewfinder --
from unihiker_k10 import screen  # noqa: E402

BG    = 0x0C0E1C
WHITE = 0xFFFFFF
GOLD  = 0xC9A227
GREEN = 0x3CDC78
GRAY  = 0x505564
W, H  = 240, 320

screen.init(dir=2)
screen.stop_camera()
screen.show_camera(_cam)

# -- button --
_btn_a = Pin(5, Pin.IN, Pin.PULL_UP)


def flash_screen():
    """Brief white flash feedback."""
    screen.stop_camera()
    screen.show_bg(color=WHITE)
    screen.show_draw()
    utime.sleep_ms(60)
    screen.show_camera(_cam)


def show_uploading(n):
    screen.stop_camera()
    screen.show_bg(color=BG)
    screen.draw_text(text=f"uploading {n} photos", x=24, y=H // 2 - 20, font_size=16, color=GOLD)
    screen.draw_text(text="please wait...", x=54, y=H // 2 + 10, font_size=14, color=GRAY)
    screen.show_draw()


def _post(url, data, headers, timeout_s=10):
    """POST with socket-level timeout."""
    import usocket
    proto, _, host_port, path = url.split("/", 3)
    path = "/" + path
    if ":" in host_port:
        host, port = host_port.split(":")
        port = int(port)
    else:
        host, port = host_port, 80

    addr = usocket.getaddrinfo(host, port)[0][-1]
    sock = usocket.socket()
    sock.settimeout(timeout_s)
    sock.connect(addr)

    content_len = len(data)
    req = f"POST {path} HTTP/1.0\r\nHost: {host}\r\nContent-Length: {content_len}\r\n"
    for k, v in headers.items():
        req += f"{k}: {v}\r\n"
    req += "\r\n"
    sock.send(req.encode())

    mv = memoryview(data)
    sent = 0
    while sent < content_len:
        chunk = mv[sent:sent + 4096]
        sock.send(chunk)
        sent += len(chunk)

    resp = b""
    while b"\r\n" not in resp:
        resp += sock.recv(128)
    status = int(resp.split(b" ")[1])
    sock.close()
    return status


def upload_and_reset(frames):
    """Deinit screen, connect WiFi, upload, then reset for fresh viewfinder."""
    show_uploading(len(frames))
    screen.deinit()
    utime.sleep_ms(200)

    ok_count = 0
    try:
        wifi = WiFi()
        wifi.connect(ssid=SSID, psd=PASSWORD, timeout=15000)
        if not wifi.status():
            print("wifi failed")
        else:
            for raw in frames:
                try:
                    status = _post(
                        SERVER_URL + "/upload",
                        raw,
                        {
                            "Content-Type": "image/x-rgb565",
                            "X-Width": "240",
                            "X-Height": "320",
                            "X-Cam-Id": CAM_ID,
                            "X-Event": EVENT,
                        },
                        timeout_s=10,
                    )
                    if status == 200:
                        ok_count += 1
                except Exception as e:
                    print("err:", e)
            print(f"uploaded {ok_count}/{len(frames)}")
    except Exception as e:
        print("batch err:", e)

    # Reset for a fresh viewfinder on next boot
    utime.sleep_ms(500)
    machine.reset()


# -- main loop --
print("booth ready -", CAM_ID)
buffer = []
armed = True

while True:
    if _btn_a.value() == 1:
        if armed:
            armed = False
            raw = _cam.camera_capture()
            buffer.append(raw)
            flash_screen()
            print(f"captured ({len(buffer)}/{BATCH_SIZE})")
            if len(buffer) >= BATCH_SIZE:
                upload_and_reset(buffer)
    else:
        armed = True
    utime.sleep_ms(40)
