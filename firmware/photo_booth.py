# UniHiker K10 — Polaroid Photo Booth (static screen + batched upload)
# MicroPython (k10_micropython_v0.9.8 / MicroPython 1.26). Deploy as /main.py.
#
# PRESS BUTTON A to capture. Screen shows status and last capture preview.
# Every 5 captures: upload batch → machine.reset() for clean state.
#
# Screen states:
#   - Boot: "PRESS A TO SNAP" with cam ID and event
#   - After capture: shows the captured image briefly, then "X/5" counter
#   - Upload: "uploading N photos..."
#
# No live viewfinder (too laggy on this hardware). Static UI only.

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

# -- screen --
from unihiker_k10 import screen  # noqa: E402

BG    = 0x0C0E1C
WHITE = 0xFFFFFF
GOLD  = 0xC9A227
GREEN = 0x3CDC78
RED   = 0xE63C3C
GRAY  = 0x505564
W, H  = 240, 320

screen.init(dir=2)
screen.stop_camera()


def show_ready(count=0):
    """Show idle screen with capture count."""
    screen.show_bg(color=BG)
    screen.draw_text(text=CAM_ID, x=6, y=6, font_size=14, color=GOLD)
    if count == 0:
        screen.draw_text(text="PRESS A", x=62, y=120, font_size=22, color=WHITE)
        screen.draw_text(text="TO SNAP", x=62, y=155, font_size=22, color=WHITE)
    else:
        screen.draw_text(text=f"{count}/{BATCH_SIZE}", x=90, y=110, font_size=28, color=WHITE)
        screen.draw_text(text="photos taken", x=58, y=155, font_size=16, color=GRAY)
        screen.draw_text(text="press A for more", x=42, y=185, font_size=14, color=GOLD)
    screen.draw_text(text=EVENT[:22], x=6, y=296, font_size=14, color=GRAY)
    screen.show_draw()


def show_flash():
    """Brief white flash."""
    screen.show_bg(color=WHITE)
    screen.show_draw()
    utime.sleep_ms(80)


def show_captured(count):
    """Show capture confirmation."""
    screen.show_bg(color=BG)
    screen.draw_text(text="SNAP!", x=78, y=130, font_size=24, color=GREEN)
    screen.draw_text(text=f"{count}/{BATCH_SIZE}", x=90, y=175, font_size=18, color=WHITE)
    screen.show_draw()
    utime.sleep_ms(600)


def show_uploading(n):
    screen.show_bg(color=BG)
    screen.draw_text(text=f"uploading {n}", x=46, y=H // 2 - 30, font_size=18, color=GOLD)
    screen.draw_text(text="photos...", x=68, y=H // 2 + 5, font_size=16, color=GOLD)
    screen.draw_text(text="please wait", x=58, y=H // 2 + 40, font_size=14, color=GRAY)
    screen.show_draw()


# -- button --
_btn_a = Pin(5, Pin.IN, Pin.PULL_UP)


# -- upload --

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
    """Deinit screen, connect WiFi, upload, then reset."""
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

    utime.sleep_ms(500)
    machine.reset()


# -- main loop --
show_ready(0)
print("booth ready -", CAM_ID)
buffer = []
armed = True

while True:
    if _btn_a.value() == 1:
        if armed:
            armed = False
            show_flash()
            raw = _cam.camera_capture()
            buffer.append(raw)
            count = len(buffer)
            print(f"captured ({count}/{BATCH_SIZE})")
            show_captured(count)
            if count >= BATCH_SIZE:
                upload_and_reset(buffer)
            else:
                show_ready(count)
            # Debounce: wait for release + cooldown
            while _btn_a.value() == 1:
                utime.sleep_ms(20)
            utime.sleep_ms(200)
    else:
        armed = True
    utime.sleep_ms(40)
