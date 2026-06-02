# K10 Recovery — Bricked / Hung / Corrupt Filesystem

Use this when the K10 is unresponsive: serial writes time out, display frozen,
`mpremote` can't enter raw REPL, or filesystem corruption loop on boot.

esptool talks to the ESP32-S3 ROM over USB-Serial-JTAG — no BOOT button needed,
no MicroPython dependency.

## Symptoms That Mean "Go To esptool"

- Boot output then silence; serial writes time out
- Half-painted/frozen display, no response to Ctrl+C
- `mpremote ... could not enter raw repl`
- `The filesystem appears to be corrupted` loop
- Guru Meditation / panic on boot

A halted CPU cannot process Ctrl+C or DTR toggles. Don't waste time on REPL tricks.

## macOS Port Discovery

```bash
ls /dev/cu.usbmodem*
# Typical: /dev/cu.usbmodem11341201
```

On Linux: `/dev/ttyACM0`

## Step 1 — Confirm esptool Reaches the Chip

```bash
esptool --chip esp32s3 --port /dev/cu.usbmodem11341201 flash-id
```

Expect: `Connected to ESP32-S3`, `USB mode: USB-Serial/JTAG`, `Detected flash
size: 16MB`. If this works, full recovery will work.

## Step 2 — Full Erase

```bash
esptool --chip esp32s3 --port /dev/cu.usbmodem11341201 erase-flash
```

Always erase fully first — partial write leaves stale data that corrupts the FS.

## Step 3 — Reflash MicroPython

```bash
esptool --chip esp32s3 --port /dev/cu.usbmodem11341201 write-flash 0x0 \
  ~/Downloads/k10_micropython_v0.9.8.bin
```

Takes ~2 min for 16MB. Wait for `Hash of data verified`.

## Step 4 — Reformat Filesystem (REQUIRED)

After reflash the device reports `The filesystem appears to be corrupted`.
Reformat via paste mode (mpremote can't enter raw REPL through the spam):

```bash
python3 -c "
import serial, time, os
port = '/dev/cu.usbmodem11341201'  # adjust if different
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
# paste mode
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

## Step 5 — Verify mpremote Works

```bash
mpremote connect /dev/cu.usbmodem11341201 ls
```

Should return cleanly (empty filesystem). Device is now ready for `/k10-deploy`.

## Notes

- FS API: `vfs.VfsLfs2` (LittleFS2), block device = `flashbdev.bdev`
- `umount('/')` raises EINVAL if nothing mounted — safe to ignore
- esptool hard-resets via RTS at end of each command (triggers cold boot)
- Force hard reset any time: `esptool --chip esp32s3 --port PORT --after hard-reset flash-id`
