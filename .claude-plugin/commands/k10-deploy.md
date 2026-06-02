# K10 Deploy — Firmware to a Healthy Device

Deploy `photo_booth.py` and `booth_config.py` to a K10 that is already running
MicroPython with a clean filesystem. If the device is bricked, use `/k10-recover` first.

## Prerequisites

- K10 connected via USB-C, visible at `/dev/cu.usbmodem*`
- `mpremote` installed (`pip install mpremote`)
- Repo cloned at `~/WORKSPACE/k10-polaroid-booth`
- `firmware/booth_config.py` exists (copy from `booth_config.example.py` if not)

## Find the Port

```bash
ls /dev/cu.usbmodem*
```

## Deploy (device at REPL or boot guard)

```bash
PORT=/dev/cu.usbmodem11341201   # adjust
mpremote connect $PORT cp ~/WORKSPACE/k10-polaroid-booth/firmware/photo_booth.py :main.py
mpremote connect $PORT cp ~/WORKSPACE/k10-polaroid-booth/firmware/booth_config.py :booth_config.py
```

The firmware deploys as `/main.py` so it autoruns on boot.

## If a Hung main.py Blocks mpremote

The boot guard (`select.poll(3000)`) gives a 3-second window where any byte
drops the device to REPL. mpremote's handshake bytes trip this automatically.

If the device is hung (main.py crashed past the guard):
1. Hard-reset: `esptool --chip esp32s3 --port $PORT --after hard-reset flash-id`
2. Immediately retry mpremote (within 3s of reset):
   ```bash
   mpremote connect $PORT cp firmware/photo_booth.py :main.py
   ```
3. May need 3-4 attempts — loop it:
   ```bash
   for i in 1 2 3 4; do
     mpremote connect $PORT cp firmware/photo_booth.py :main.py && break
     sleep 0.5
   done
   ```

## Per-Device Config Patching

Each K10 gets a unique `CAM_ID` (cam1, cam2, ... cam6). Edit `booth_config.py`
before deploying to each unit:

```python
SSID      = "bluto"
PASSWORD  = "..."
SERVER_URL = "http://192.168.86.193:8080"
CAM_ID    = "cam1"   # change per device
EVENT     = "Ender's Party - June 2026"
```

Or patch on the fly:
```bash
sed "s/CAM_ID.*/CAM_ID    = \"cam2\"/" firmware/booth_config.py > /tmp/booth_config_cam2.py
mpremote connect $PORT cp /tmp/booth_config_cam2.py :booth_config.py
```

## Verify

After deploy, hard-reset and watch serial for ~10s:
```bash
esptool --chip esp32s3 --port $PORT --after hard-reset flash-id
# wait 4s for boot guard to pass, then:
python3 -c "
import serial, time
s = serial.Serial('$PORT', 115200, timeout=0.2)
end = time.time() + 15
buf = b''
while time.time() < end:
    buf += s.read(512) or b''
print(buf.decode('utf-8', errors='replace'))
s.close()
"
```

Look for: `booth boot guard`, `wifi ok` or `wifi failed`, then the main loop starts.
