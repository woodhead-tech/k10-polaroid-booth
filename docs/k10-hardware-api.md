# K10 hardware API — verified on MicroPython 1.26 / k10_micropython_v0.9.8

Everything here was confirmed empirically at the REPL on real hardware. Do not
substitute guessed APIs (e.g. `cam.capture()`, `cam.snapshot()`) — they are wrong
on this firmware.

## Modules

- `k10_base` — exposes `Camera`, `Button`, `Screen`, `WiFi`, `AHT20`, `Mic`,
  `Speaker`, `Light`, `MQTT`, `SDCard`, plus low-level `Pin`, `I2C`, `SPI`, etc.
- `unihiker_k10` — package providing the ready `screen` singleton.
  Use `from unihiker_k10 import screen` ONLY. (See traps below.)
- `flashbdev`, `vfs` — filesystem (see recovery.md).

Probe anything with: `mpremote connect /dev/ttyACM0 exec "import k10_base; print(dir(k10_base.Camera))"`

## Camera

```python
from k10_base import Camera
cam = Camera()
cam.init()                      # call once before use; emits "I (NNNN) camera: cam_init ok"
raw = cam.camera_capture()      # -> 153600 bytes raw RGB565 (240x320, BGR;16 little-endian)
cam.save(path)                  # also available
```

Methods: `camera_capture`, `init`, `save`.

153600 = 240 × 320 × 2 bytes/pixel. The byte order decodes in PIL as
`Image.frombuffer("RGB", (240,320), raw, "raw", "BGR;16", 0, 1)` — this is what
the photo-booth server's `decode_frame()` does.

## Button

```python
from k10_base import Button
btn = Button(0)                 # HOME button = GPIO0
btn.is_pressed()                # -> bool (level now)
btn.was_pressed()               # -> bool (edge since last check)
btn.get_presses()               # -> count
```

Methods: `value`, `irq`, `status`, `check_state`, `get_presses`, `is_pressed`,
`was_pressed`.

## Screen

```python
from unihiker_k10 import screen          # the working singleton
screen.init(dir=2)                        # dir=2 = portrait 240x320
screen.show_bg(color=0x0C0E1C)            # fill background (RGB565 hex)
screen.draw_rect(x=, y=, w=, h=, bcolor=, fcolor=)
screen.draw_text(text="ASCII ONLY", x=, y=, font_size=, color=)
screen.draw_circle(...) ; screen.draw_line(...) ; screen.draw_point(...)
screen.show_draw()                        # flush drawing to the panel
screen.clear()
```

Methods: `init`, `clear`, `deinit`, `set_width`, `draw_line`, `draw_rect`,
`draw_circle`, `draw_point`, `draw_text`, `draw_sys_img`, `show_bg`, `show_draw`,
`show_camera`, `show_camera_feed`, `show_camera_img`, `show_camera_img_safe`,
`stop_camera`.

The `show_camera_feed` / `show_camera_img_safe` methods can render a live camera
preview to the panel — useful for a viewfinder before a photo-booth capture.

## WiFi

```python
from k10_base import WiFi
wifi = WiFi()
wifi.connect(ssid="bluto", psd="...", timeout=30000)   # BLOCKING up to timeout
wifi.status()                                          # -> truthy when connected
```

`connect()` is a **blocking** call. During it the asyncio scheduler does not run
and Ctrl+C cannot land — relevant when debugging a no-network hang.

## Hard-fault traps (each one halts/reboots the CPU)

| Don't | Why | Do instead |
|-------|-----|-----------|
| `import unihiker_k10` (bare) | InstructionFetchError, reboot | `from unihiker_k10 import screen` |
| `k10_base.Screen()` (instantiate) | LoadProhibited, reboot | use the `screen` singleton above |
| `draw_text` with non-ASCII (`·`, smart quotes, em-dash) | suspected font-renderer fault → LoadProhibited | keep all on-screen strings 7-bit ASCII |
| Soft reset (Ctrl+D) to re-run firmware | re-init on a live camera/display faults | cold/hard reset (`esptool --after hard-reset ...`) |

## REPL access patterns

- **`mpremote run script.py`** — safest test path: runs in a clean REPL on
  already-initialized hardware, shows real tracebacks. Behaves differently from
  boot-time `main.py` execution (peripheral state differs).
- **`mpremote cp x :main.py`** — install for autorun at boot.
- Both require the device NOT be running a crashing `main.py`. If it is, recover
  first (recovery.md).
- USB-Serial-JTAG quirk: a halted CPU drains nothing; serial writes time out.
  That is the "go to esptool" signal.

## Boot guard (active-poll — put in every firmware)

`utime.sleep(3)` does NOT yield an interrupt window (sleep never reads stdin).
Use an active poll so any keypress drops to the REPL before the app starts:

```python
import sys, select
_p = select.poll(); _p.register(sys.stdin, select.POLLIN)
if _p.poll(3000):
    sys.exit()        # abort to REPL
```
