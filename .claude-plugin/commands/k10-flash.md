# K10 Flash — Full Flash From Blank

Complete sequence: erase chip, write MicroPython firmware, reformat filesystem,
deploy booth code. Use for a brand-new K10 or one that needs a clean start.

This combines `/k10-recover` + `/k10-deploy` into one end-to-end flow.

## Prerequisites

- K10 connected via USB-C
- `esptool` installed (`pip install esptool`)
- `mpremote` installed (`pip install mpremote`)
- Firmware image: `~/Downloads/k10_micropython_v0.9.8.bin`
- Repo: `~/WORKSPACE/k10-polaroid-booth` with `firmware/booth_config.py`

## Full Sequence

```bash
PORT=/dev/cu.usbmodem11341201   # adjust: ls /dev/cu.usbmodem*

# 1. Confirm chip reachable
esptool --chip esp32s3 --port $PORT flash-id

# 2. Erase (required — partial writes corrupt FS)
esptool --chip esp32s3 --port $PORT erase-flash

# 3. Write MicroPython (~2 min)
esptool --chip esp32s3 --port $PORT write-flash 0x0 ~/Downloads/k10_micropython_v0.9.8.bin

# 4. Reformat filesystem (see below — paste mode script)

# 5. Deploy firmware
mpremote connect $PORT cp ~/WORKSPACE/k10-polaroid-booth/firmware/photo_booth.py :main.py
mpremote connect $PORT cp ~/WORKSPACE/k10-polaroid-booth/firmware/booth_config.py :booth_config.py
```

## Step 4 Detail — Reformat FS

After `write-flash` the device spams `filesystem appears to be corrupted`.
Run this Python script to reformat via paste mode:

```bash
python3 -c "
import serial, time
port = '/dev/cu.usbmodem11341201'  # adjust
s = serial.Serial(port, 115200, timeout=0.1, write_timeout=1,
                  dsrdtr=False, rtscts=False, xonxoff=False)
time.sleep(0.3)
def drain(t=1.0):
    end=time.time()+t; b=b''
    while time.time()<end:
        d=s.read(512)
        if d: b+=d
    return b
s.write(b'\x03'); time.sleep(0.1)
s.write(b'\x03'); time.sleep(0.1)
s.write(b'\x02'); time.sleep(0.4); drain(1.5)
s.write(b'\x05'); time.sleep(0.3); drain(0.5)
script = (
    'import vfs, flashbdev\n'
    'try:\n'
    '    vfs.umount(\"/\")\n'
    'except Exception as e:\n'
    '    print(\"umount skip:\", e)\n'
    'vfs.VfsLfs2.mkfs(flashbdev.bdev)\n'
    'print(\"MKFS OK\")\n'
    'vfs.mount(vfs.VfsLfs2(flashbdev.bdev), \"/\")\n'
    'print(\"MOUNT OK\")\n'
    'import os\n'
    'print(\"LS\", os.listdir(\"/\"))\n'
)
s.write(script.encode()); time.sleep(0.3)
s.write(b'\x04')
print(drain(6.0).decode(errors='replace'))
s.close()
"
```

Expect: `MKFS OK`, `MOUNT OK`, `LS []`.

## Verify End-to-End

```bash
mpremote connect $PORT ls
# Should show: main.py, booth_config.py

# Hard-reset to boot into booth mode:
esptool --chip esp32s3 --port $PORT --after hard-reset flash-id
```

Watch serial for `booth boot guard` → `wifi ok` → live viewfinder starts.

## Fleet Flash (Multiple Units)

For flashing cam1 through cam6:
```bash
for i in 1 2 3 4 5 6; do
  echo "=== Plug in K10 for cam${i}, press Enter ==="
  read
  PORT=$(ls /dev/cu.usbmodem* | head -1)
  sed "s/CAM_ID.*/CAM_ID    = \"cam${i}\"/" firmware/booth_config.py > /tmp/booth_config_cam${i}.py
  mpremote connect $PORT cp firmware/photo_booth.py :main.py
  mpremote connect $PORT cp /tmp/booth_config_cam${i}.py :booth_config.py
  echo "cam${i} done"
done
```
