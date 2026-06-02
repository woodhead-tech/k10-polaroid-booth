#!/bin/bash
# Flash photo booth firmware to K10 via serial.
#
# Usage:
#   CAM_ID=cam1 ./flash.sh
#   CAM_ID=cam2 ./flash.sh
#
# Requires one physical USB-C unplug+replug — catches the 3s boot guard.
# No WiFi needed; files are written directly over serial.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CAM_ID="${CAM_ID:-cam1}"

echo "=== K10 Polaroid Booth Flash ==="
echo "CAM_ID: ${CAM_ID}"
echo ""
echo "Unplug the K10 USB-C now, then replug when prompted..."

python3 - "$SCRIPT_DIR" "$CAM_ID" << 'PYEOF'
import sys, os, time, serial, glob, base64, re

src_dir = sys.argv[1]
cam_id  = sys.argv[2]

# ── Load files ──────────────────────────────────────────────────────────────
booth_py = open(os.path.join(src_dir, 'photo_booth.py')).read()

config_src = open(os.path.join(src_dir, 'booth_config.py')).read()
config_py = re.sub(r'^CAM_ID\s*=.*$', f'CAM_ID    = "{cam_id}"',
                   config_src, flags=re.MULTILINE)

# ── Wait for device to disappear (user unplugs) ─────────────────────────────
print("Waiting for unplug...", flush=True)
for _ in range(120):
    if not glob.glob('/dev/ttyACM*'):
        break
    time.sleep(0.25)
else:
    # If device was never connected, skip the wait
    pass

print("Waiting for replug...", flush=True)
port = None
for _ in range(120):
    ports = sorted(glob.glob('/dev/ttyACM*'))
    if ports:
        port = ports[-1]
        print(f"Device appeared: {port}")
        break
    time.sleep(0.15)

if not port:
    print("ERROR: no device found. Check USB cable.")
    sys.exit(1)

# ── Open WITHOUT asserting DTR (avoid extra reset) ─────────────────────────
# No stabilization delay — we need to catch the 3s boot guard immediately.
s = serial.Serial(port, 115200, timeout=0.05, write_timeout=None,
                  dsrdtr=False, rtscts=False, xonxoff=False)
s.reset_input_buffer()

buf = b''
fd = s.fileno()

def drain(secs=0.1):
    global buf
    t0 = time.time()
    while time.time() - t0 < secs:
        d = s.read(256)
        if d:
            buf += d

def try_write(b):
    """Non-blocking write via os.write; ignores errors."""
    import select
    try:
        if select.select([], [fd], [], 0.01)[1]:
            os.write(fd, b)
    except:
        pass

# ── Spam Ctrl+C for 4s (covers the utime.sleep(3) boot guard) ─────────────
print("Sending Ctrl+C to interrupt boot guard...", flush=True)
t0 = time.time()
while time.time() - t0 < 4.0:
    if b'raw REPL' in buf:
        break
    try_write(b'\x03')
    time.sleep(0.02)
    drain(0.01)

# ── Enter raw REPL ─────────────────────────────────────────────────────────
try_write(b'\x01')
time.sleep(1.5)
drain(1.0)

if b'raw REPL' not in buf:
    print(f"ERROR: could not enter raw REPL.\nDevice output: {repr(buf[-400:])}")
    print("\nTip: unplug and replug USB-C, then run this script again within 2s.")
    os.close(s.fileno())
    sys.exit(1)

print("In raw REPL — writing files...", flush=True)

def raw_exec(code, wait=4.0):
    """Send code to raw REPL, return output."""
    global buf
    mark = len(buf)
    payload = code.encode() + b'\x04'
    # Write in small chunks with short delays
    chunk = 64
    for i in range(0, len(payload), chunk):
        try:
            s.write(payload[i:i+chunk])
        except:
            try_write(payload[i:i+chunk])
        time.sleep(0.01)
    time.sleep(wait)
    drain(0.5)
    return buf[mark:].decode(errors='replace')

def write_file(remote_path, content, label):
    """Write a text file to the device via raw REPL."""
    # Encode as base64 to avoid quote/escape issues
    b64 = base64.b64encode(content.encode()).decode()
    # Write in one shot if small, else chunk it
    chunk = 800  # bytes of base64 per chunk
    code = f"""
import ubinascii, os
data = b''
"""
    for i in range(0, len(b64), chunk):
        code += f"data += ubinascii.a2b_base64(b'{b64[i:i+chunk]}')\n"
    code += f"""
f = open('{remote_path}', 'wb')
f.write(data)
f.close()
print('wrote', len(data), 'bytes to {remote_path}')
"""
    out = raw_exec(code, wait=max(5.0, len(b64) / 4000 + 3))
    print(f"  {label}: {out.strip()}")

write_file('/main.py',         booth_py,  'photo_booth → main.py')
write_file('/booth_config.py', config_py, 'booth_config.py')

# Verify
out = raw_exec("import os; files = os.listdir('/'); print([f for f in files if f.endswith('.py')])", 2)
print(f"\nFiles on device: {out.strip()}")

# Soft reset to run new firmware
s.write(b'\x04')  # Ctrl+D = soft reset in raw REPL
time.sleep(0.5)

os.close(s.fileno())
print(f"\nDone! CAM_ID={cam_id} flashed.")
print("The K10 will now boot into photo booth mode.")
print("Check serial output for camera API discovery on first button press.")
PYEOF
