#!/bin/bash
# Upload photo_booth.py + booth_config.py to a K10 via WiFi.
#
# Usage:
#   CAM_ID=cam1 ./upload_booth.sh
#   CAM_ID=cam2 ./upload_booth.sh
#
# Steps:
#   1. Set CAM_ID env var (cam1 … cam6)
#   2. Run this script
#   3. Unplug and replug the K10 USB-C when prompted
#   The K10 downloads both files from this machine over WiFi.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CAM_ID="${CAM_ID:-cam1}"

echo "Flashing booth firmware — CAM_ID=${CAM_ID}"

python3 - << PYEOF
import os, time, serial, http.server, threading, socket, re

cam_id   = '${CAM_ID}'
src_dir  = '${SCRIPT_DIR}'

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("192.168.86.193", 80))
    ip = s.getsockname()[0]
    s.close()
    return ip

local_ip = get_local_ip()
port     = 8766
print(f"Serving files at http://{local_ip}:{port}/")

# Patch CAM_ID into booth_config.py on the fly
config_src = open(os.path.join(src_dir, 'booth_config.py')).read()
config_patched = re.sub(r'^CAM_ID\s*=.*$', f"CAM_ID    = \"{cam_id}\"",
                         config_src, flags=re.MULTILINE)
patched_path = '/tmp/booth_config_patched.py'
open(patched_path, 'w').write(config_patched)

# Serve from two dirs by symlinking
tmp_dir = '/tmp/booth_serve'
os.makedirs(tmp_dir, exist_ok=True)
for fname in ('photo_booth.py',):
    dst = os.path.join(tmp_dir, fname)
    if os.path.exists(dst): os.remove(dst)
    os.symlink(os.path.join(src_dir, fname), dst)
# serve patched config as booth_config.py
dst = os.path.join(tmp_dir, 'booth_config.py')
if os.path.exists(dst): os.remove(dst)
os.symlink(patched_path, dst)

server = http.server.HTTPServer(('', port),
    lambda *a: http.server.SimpleHTTPRequestHandler(*a, directory=tmp_dir))
t = threading.Thread(target=server.serve_forever, daemon=True)
t.start()

print("Waiting for device — unplug and replug USB-C now...")
for _ in range(60):
    if os.path.exists('/dev/ttyACM0'):
        break
    time.sleep(0.3)
else:
    print("No device found"); exit(1)

with serial.Serial('/dev/ttyACM0', 115200, timeout=1) as s:
    end = time.time() + 4
    while time.time() < end:
        s.write(b'\x03')
        time.sleep(0.02)
    s.write(b'\x01')
    time.sleep(0.5)
    out = s.read_all()
    if b'raw REPL' not in out:
        print("Couldn't enter REPL — try again"); exit(1)
    print("In REPL — downloading files...")

    base = f"http://{local_ip}:{port}"
    cmd = f"""
import network, urequests, utime
w = network.WLAN(network.STA_IF)
w.active(True)
if not w.isconnected():
    w.connect('bluto', '1Pu113e@L1bby!')
    for _ in range(30):
        if w.isconnected(): break
        utime.sleep(1)
if w.isconnected():
    for src, dst in (
        ('{base}/photo_booth.py',  '/photo_booth.py'),
        ('{base}/booth_config.py', '/booth_config.py'),
    ):
        r = urequests.get(src, timeout=20)
        open(dst, 'w').write(r.text)
        r.close()
    print('ok')
else:
    print('wifi failed')
"""
    s.write(cmd.encode() + b'\x04')
    time.sleep(60)

server.shutdown()
print(f"Done — CAM_ID={cam_id} flashed. Press RST on the K10 to run.")
PYEOF
