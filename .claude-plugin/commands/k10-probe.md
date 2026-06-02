# K10 Probe — REPL Connection and API Testing

Connect to the K10 interactively, execute code via paste mode, and test hardware
APIs. Use when experimenting with new features or debugging behavior.

## Connect to REPL (Python serial)

```python
import serial, time

port = '/dev/cu.usbmodem11341201'  # ls /dev/cu.usbmodem*
s = serial.Serial(port, 115200, timeout=1.0, write_timeout=1,
                  dsrdtr=False, rtscts=False, xonxoff=False)
time.sleep(0.3)

# Interrupt whatever's running
s.write(b'\x03'); time.sleep(0.2)
s.write(b'\x03'); time.sleep(1.0)

data = b''
while True:
    chunk = s.read(1024)
    if not chunk: break
    data += chunk
print(data.decode('utf-8', errors='replace'))
s.close()
```

If you see `>>>` — you're in the REPL.

## Send Single Commands

```python
def send_cmd(cmd, wait=2.0):
    s.reset_input_buffer()
    s.write((cmd + '\r\n').encode())
    time.sleep(wait)
    data = b''
    while True:
        chunk = s.read(2048)
        if not chunk: break
        data += chunk
    return data.decode('utf-8', errors='replace')

print(send_cmd('print("hello")'))
```

## Paste Mode (Multi-line Code)

The REPL's line mode doesn't handle multi-line blocks well. Use paste mode
(Ctrl+E to enter, Ctrl+D to execute):

```python
def paste_exec(code, wait=4.0):
    s.reset_input_buffer()
    s.write(b'\x05')      # Ctrl+E = enter paste mode
    time.sleep(0.5)
    s.write(code.encode())
    time.sleep(0.3)
    s.write(b'\x04')      # Ctrl+D = execute
    time.sleep(wait)
    data = b''
    while True:
        chunk = s.read(2048)
        if not chunk: break
        data += chunk
    return data.decode('utf-8', errors='replace')

result = paste_exec('''
import utime
print("starting")
utime.sleep_ms(500)
print("done")
''')
print(result)
```

## Common Probe Sequences

### Init camera + screen + viewfinder
```python
paste_exec('''
from k10_base import Camera
cam = Camera()
cam.init()
from unihiker_k10 import screen
screen.init(dir=2)
screen.show_camera(cam)
print("viewfinder running")
''')
```

### Test touch detection
```python
paste_exec('''
import k10_base, utime
_i2c = k10_base.k10_i2c
for i in range(50):
    val = _i2c.readfrom_mem(0x38, 0x02, 1)[0] & 0x0F
    if val > 0:
        print(f"TOUCH at iteration {i}")
    utime.sleep_ms(100)
print("done")
''')
```

### Test capture + upload
```python
paste_exec('''
import urequests
raw = cam.camera_capture()
print("captured", len(raw), "bytes")
r = urequests.post(
    "http://192.168.86.193:8080/upload",
    data=raw,
    headers={
        "Content-Type": "image/x-rgb565",
        "X-Width": "240", "X-Height": "320",
        "X-Cam-Id": "cam1", "X-Event": "test"
    })
print("status:", r.status_code)
r.close()
''', wait=10.0)
```

### Full booth cycle (stop feed → flash → draw → restart)
```python
paste_exec('''
import utime
raw = cam.camera_capture()
screen.stop_camera()
screen.show_bg(color=0xFFFFFF)
screen.show_draw()
utime.sleep_ms(80)
screen.show_bg(color=0x0C0E1C)
screen.draw_text(text="uploading...", x=52, y=150, font_size=16, color=0xC9A227)
screen.show_draw()
utime.sleep_ms(500)
screen.show_camera(cam)
print("cycle ok, captured", len(raw), "bytes")
''', wait=8.0)
```

### Explore unknown attributes
```python
send_cmd('dir(screen)')
send_cmd('[x for x in dir(cam) if not x.startswith("_")]')
send_cmd('print(type(screen.img_dsc), screen.img_dsc)')
```

## Full Reusable Probe Script Template

```python
import serial, time

port = '/dev/cu.usbmodem11341201'
s = serial.Serial(port, 115200, timeout=2.0, write_timeout=1,
                  dsrdtr=False, rtscts=False, xonxoff=False)
time.sleep(0.3)

def send_cmd(cmd, wait=2.0):
    s.reset_input_buffer()
    s.write((cmd + '\r\n').encode())
    time.sleep(wait)
    data = b''
    while True:
        chunk = s.read(2048)
        if not chunk: break
        data += chunk
    return data.decode('utf-8', errors='replace')

def paste_exec(code, wait=4.0):
    s.reset_input_buffer()
    s.write(b'\x05')
    time.sleep(0.5)
    s.write(code.encode())
    time.sleep(0.3)
    s.write(b'\x04')
    time.sleep(wait)
    data = b''
    while True:
        chunk = s.read(2048)
        if not chunk: break
        data += chunk
    return data.decode('utf-8', errors='replace')

# Interrupt any running code
s.write(b'\x03'); time.sleep(0.2)
s.write(b'\x03'); time.sleep(1.0)
s.read(4096)  # drain

# --- Your probing here ---

s.close()
```

## Tips

- Always drain the buffer after interrupts before sending commands
- Paste mode handles imports, try/except, loops — anything multi-line
- Wait times: simple commands = 1-2s, camera ops = 3-4s, network = 8-10s
- If serial writes time out: device has crashed. Use `/k10-recover`
- `pyserial` required: `pip install pyserial` (or `--break-system-packages` on macOS)
