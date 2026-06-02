#!/usr/bin/env python3
"""
K10 Photo Booth Flash — reactive approach.

Steps:
  1. Run this script
  2. Unplug K10 USB-C, wait 2-3 seconds, replug
  3. Wait for the K10 display to come up (LVGL running)
  4. Script sends Ctrl+C, catches the drop-to-REPL, flashes files

Usage:
  CAM_ID=cam1 python3 reactive_flash.py
  CAM_ID=cam2 python3 reactive_flash.py
"""
import os, sys, time, serial, glob, base64, re, threading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CAM_ID = os.environ.get('CAM_ID', 'cam1')

# ── Load source files ──────────────────────────────────────────────────────
booth_py = open(os.path.join(SCRIPT_DIR, 'photo_booth.py')).read()
config_src = open(os.path.join(SCRIPT_DIR, 'booth_config.py')).read()
config_py = re.sub(r'^CAM_ID\s*=.*$', f'CAM_ID    = "{CAM_ID}"',
                   config_src, flags=re.MULTILINE)

print(f"K10 Polaroid Booth Flash — CAM_ID={CAM_ID}")
print()
print("Step 1: Unplug K10 USB-C, wait 2 seconds, replug.")
print("Step 2: Wait for display to come up on the K10 screen.")
print("Step 3: This script will do the rest automatically.")
print()

# ── Wait for device ────────────────────────────────────────────────────────
print("Waiting for K10 USB serial (/dev/ttyACM*)...", flush=True)
port = None
for _ in range(300):
    ports = sorted(glob.glob('/dev/ttyACM*'))
    if ports:
        port = ports[-1]
        break
    time.sleep(0.2)

if not port:
    print("ERROR: No device found. Plug in the K10.")
    sys.exit(1)

print(f"Found: {port}", flush=True)

# ── Wait for USB CDC to become writable (LVGL must be running) ─────────────
import select as sel_mod

print("Waiting for USB CDC to become writable (K10 display must be on)...", flush=True)
s = serial.Serial(port, 115200, timeout=0.05, write_timeout=0.1,
                  dsrdtr=False, rtscts=False, xonxoff=False)
fd = s.fileno()

writable = False
for _ in range(200):  # 20 seconds max
    rdy = sel_mod.select([], [fd], [], 0.1)
    if rdy[1]:
        writable = True
        break
    time.sleep(0.05)

if not writable:
    print("ERROR: USB CDC never became writable. Is the display running?")
    s.close()
    sys.exit(1)

print("USB CDC is writable — sending Ctrl+C to interrupt asyncio loop...", flush=True)

# ── Reactive interrupt + Ctrl+A ────────────────────────────────────────────
buf = b''
buf_lock = threading.Lock()
got_interrupt = threading.Event()
got_repl = threading.Event()

def reader():
    global buf
    while True:
        try:
            d = s.read(64)
            if d:
                with buf_lock:
                    buf += d
                sys.stdout.buffer.write(b'  RX: ' + repr(d[:80]).encode() + b'\n')
                sys.stdout.flush()
                if b'KeyboardInterrupt' in d:
                    got_interrupt.set()
                if b'raw REPL' in d:
                    got_repl.set()
                # React to >>> prompt immediately from reader thread (fastest path)
                if b'>>>' in d and not got_repl.is_set():
                    sys.stdout.buffer.write(b'  ==> >>> seen - blasting Ctrl+A from reader <==\n')
                    sys.stdout.flush()
                    for _ in range(10):
                        try:
                            s.write(b'\x01')
                        except:
                            pass
                        time.sleep(0.003)
        except:
            break

rt = threading.Thread(target=reader, daemon=True)
rt.start()

# Send Ctrl+C until interrupt seen (wait up to 60s — covers 30s wifi timeout)
interrupted = False
for i in range(600):
    try:
        s.write(b'\x03')
    except:
        pass
    if got_interrupt.wait(timeout=0.1):
        print(f"==> KeyboardInterrupt after {i+1} attempts <==", flush=True)
        interrupted = True
        break
    if got_repl.is_set():
        break  # reader thread already got us in

# Wait for raw REPL — reader thread sends Ctrl+A on seeing >>>
# (may happen before or without a formal interrupt if device was already at >>>)
got_repl.wait(timeout=8.0)

with buf_lock:
    b = buf

if b'raw REPL' not in b:
    if not interrupted:
        print("ERROR: Never got KeyboardInterrupt. Is asyncio running on the K10?")
    else:
        print(f"ERROR: Couldn't enter raw REPL. buf tail: {repr(b[-300:])}")
    os.close(fd)
    sys.exit(1)

print("\n=== IN RAW REPL — writing files ===", flush=True)


def raw_exec(code: str, wait: float = 4.0) -> str:
    global buf
    with buf_lock:
        mark = len(buf)
    payload = code.encode() + b'\x04'
    chunk = 64
    for i in range(0, len(payload), chunk):
        try:
            s.write(payload[i:i+chunk])
        except Exception as e:
            print(f"  write error: {e}")
        time.sleep(0.005)
    time.sleep(wait)
    with buf_lock:
        return buf[mark:].decode(errors='replace')


def write_file(remote_path, content, label):
    b64 = base64.b64encode(content.encode()).decode()
    chunk = 600
    code = "import ubinascii\ndata = b''\n"
    for i in range(0, len(b64), chunk):
        code += f"data += ubinascii.a2b_base64(b'{b64[i:i+chunk]}')\n"
    code += f"f = open('{remote_path}', 'wb'); f.write(data); f.close()\n"
    code += f"print('wrote', len(data), 'bytes')\n"
    out = raw_exec(code, wait=max(6.0, len(b64) / 2000 + 3))
    print(f"  {label}: {out.strip()}", flush=True)


print("Writing photo_booth.py as main.py...", flush=True)
write_file('/main.py', booth_py, 'photo_booth → /main.py')

print("Writing booth_config.py...", flush=True)
write_file('/booth_config.py', config_py, 'booth_config → /booth_config.py')

# Verify
out = raw_exec("import os; print([f for f in os.listdir('/') if f.endswith('.py')])", 2)
print(f"\nFiles on device: {out.strip()}", flush=True)

# Soft reset
print("Soft-resetting device...", flush=True)
try:
    s.write(b'\x04')
except:
    pass
time.sleep(0.5)

os.close(fd)
print(f"\nDone! CAM_ID={CAM_ID} flashed. K10 will boot into photo booth mode.")
print("Check serial output (mpremote connect /dev/ttyACM0 repl) for camera API on first button press.")
