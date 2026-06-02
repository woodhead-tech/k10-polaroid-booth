# K10 recovery — bricked / unresponsive / corrupt filesystem

The deterministic recovery path. Works regardless of what bad `main.py` is on the
device, because esptool talks to the ESP32-S3 ROM/stub over USB-Serial-JTAG and
does not depend on MicroPython running.

## When to use this

Use recovery (not serial REPL tricks) the moment you see any of:
- Frozen / half-painted display, device unresponsive
- Boot output then silence; serial **writes time out**
- `mpremote ... could not enter raw repl`
- `The filesystem appears to be corrupted` loop
- A flashed `main.py` that hard-faults (Guru Meditation / panic) on boot

A halted CPU (post-panic) cannot process Ctrl+C, raw REPL, or DTR toggles. Do not
waste time on them.

## Step 0 — free the port

```bash
fuser -k /dev/ttyACM0; sleep 1
```

## Step 1 — confirm esptool reaches the chip (non-destructive)

```bash
esptool --chip esp32s3 --port /dev/ttyACM0 flash-id
```

Expect: `Connected to ESP32-S3`, `USB mode: USB-Serial/JTAG`, `Detected flash
size: 16MB`. **No BOOT button required** — esptool resets into download mode over
USB-Serial-JTAG by itself. If this works, full recovery will work.

## Step 2 — full erase

A plain `write-flash` only erases the region it writes (~15.7MB of a 16MB chip),
leaving stale data that corrupts the filesystem. Always erase fully first.

```bash
esptool --chip esp32s3 --port /dev/ttyACM0 erase-flash
```

## Step 3 — reflash MicroPython

```bash
esptool --chip esp32s3 --port /dev/ttyACM0 write-flash 0x0 \
  ~/Downloads/k10_micropython_v0.9.8.bin
```

The image starts with magic byte `0xe9` and flashes at `0x0` (ESP32-S3 bootloader
offset). Takes ~2 min for 16MB. Wait for `Hash of data verified`.

## Step 4 — reformat the filesystem (REQUIRED)

After reflash the device still reports `The filesystem appears to be corrupted` —
the firmware image does not lay down a mountable FS. Reformat from the REPL.

`mpremote` cannot enter **raw** REPL through the boot-time corruption spam, so
drive the **friendly** REPL with paste mode (Ctrl+E … Ctrl+D), which handles the
multi-line block cleanly:

```bash
fuser -k /dev/ttyACM0; sleep 1
python3 - << 'EOF'
import serial, time, os
s = serial.Serial('/dev/ttyACM0', 115200, timeout=0.1, write_timeout=1,
                  dsrdtr=False, rtscts=False, xonxoff=False)
time.sleep(0.3)
def drain(t=1.0):
    end=time.time()+t; b=b''
    while time.time()<end:
        d=s.read(512)
        if d: b+=d
    return b
# clear continuation, get friendly REPL, discard boot spam
s.write(b'\x03'); time.sleep(0.1)
s.write(b'\x03'); time.sleep(0.1)
s.write(b'\x02'); time.sleep(0.4); drain(1.5)
# paste mode
s.write(b'\x05'); time.sleep(0.3); drain(0.5)
script = (
"import vfs, flashbdev\n"
"try:\n"
"    vfs.umount('/')\n"
"except Exception as e:\n"
"    print('umount skip:', e)\n"
"vfs.VfsLfs2.mkfs(flashbdev.bdev)\n"
"print('MKFS OK')\n"
"vfs.mount(vfs.VfsLfs2(flashbdev.bdev), '/')\n"
"print('MOUNT OK')\n"
"import os\n"
"print('LS', os.listdir('/'))\n"
)
s.write(script.encode()); time.sleep(0.3)
s.write(b'\x04')                       # Ctrl+D executes the paste
print(drain(6.0).decode(errors='replace'))
os.close(s.fileno())
EOF
```

Expect `MKFS OK`, `MOUNT OK`, `LS []`. The mkfs writes to flash via
`flashbdev.bdev`, so it persists across reboot.

## Step 5 — verify normal tooling works

```bash
fuser -k /dev/ttyACM0; sleep 1
mpremote connect /dev/ttyACM0 ls          # should return cleanly (empty)
```

The device now boots to a clean REPL with no `main.py`. Deploy firmware with
`mpremote cp`.

## Notes / gotchas

- The FS API on this firmware: `vfs` module has `VfsFat`, `VfsLfs2`, `mount`,
  `umount`; block device is `flashbdev.bdev`. LittleFS2 is correct.
- `umount('/')` raises `EINVAL` if nothing is mounted — ignore it (the try/except
  above handles it).
- Multi-line `try/except` typed line-by-line into the friendly REPL puts it in
  `...` continuation mode and swallows later commands. **Always use paste mode**
  (Ctrl+E/Ctrl+D) for blocks.
- esptool hard-resets via RTS at the end of each command; that triggers a normal
  cold boot.
- To force a clean hard reset any time:
  `esptool --chip esp32s3 --port /dev/ttyACM0 --after hard-reset flash-id`
