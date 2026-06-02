# CLAUDE.md — K10 Polaroid Photo Booth

Context for Claude Code to pick this project up on any machine. This is a
hardware project: UniHiker **K10** boards (ESP32-S3 + MicroPython, with screen /
camera / touch / WiFi) act as wireless polaroid cameras. Tap the K10 screen →
capture → POST a frame to a server → server applies a polaroid frame + caption →
photos appear live on a shared gallery web page. Built for Ender's graduation
(party 2026-06-27).

> A user-scope `/k10` Claude skill also exists (`~/.claude/skills/k10/`) with the
> same hardware knowledge plus a full recovery runbook. If it synced to this
> machine, prefer it. This file makes the repo self-contained regardless.

---

## Layout

```
firmware/
  photo_booth.py        # the MicroPython firmware (deploy as /main.py on each K10)
  booth_config.example.py # template -> copy to booth_config.py (gitignored; holds WiFi pw)
  reactive_flash.py     # legacy WiFi flash helper (superseded by mpremote, below)
  flash.sh, upload_booth.sh
server/
  server.py             # FastAPI: decodes RGB565, makes polaroid, live gallery (WebSocket)
  booth.html            # the gallery wall (navy/gold)
  run.sh, requirements.txt
```

`firmware/booth_config.py` is **gitignored** (it holds the live WiFi password).
Copy `booth_config.example.py` to `booth_config.py` and fill it in per venue.

---

## Hardware facts (hard-won — do not re-derive)

- The K10 is an **ESP32-S3 with built-in USB-Serial-JTAG** (`0x303a:0x1001`),
  one CDC at `/dev/ttyACM0`. **`esptool` controls it with NO BOOT button.**
- Firmware image: `k10_micropython_v0.9.8.bin` (MicroPython 1.26, ~16.5MB, flash
  at `0x0`). **You must copy this onto the new machine** — it is not in the repo
  (16MB binary). It lived at `~/Downloads/k10_micropython_v0.9.8.bin`. Required
  for bricked-device recovery.
- **Camera**: `camera_capture()` -> 153600 bytes raw RGB565 (240x320). Also
  `.init()`, `.save()`.
- **Screen**: `from unihiker_k10 import screen` (singleton). NEVER `import
  unihiker_k10` (bare) or `k10_base.Screen()` — both hard-fault.
- **Touch** (the capture trigger): FT6336 on `k10_base.k10_i2c` (I2C0, scl=48
  sda=47) @ addr `0x38`. A touch = `readfrom_mem(0x38, 0x02, 1)[0] & 0x0F > 0`.
- The **A/B face buttons are unusable**: they share GPIO5/GPIO11 with the
  camera's parallel bus once the camera is initialized.
- Connect **WiFi before `screen.init()`** (display framebuffer starves WiFi DMA).
- **No asyncio** — its event loop conflicts with the camera; use a sync loop.
- When a flashed `main.py` hard-faults the CPU halts and serial goes dead (boot
  output then silence, writes time out). That's the "go to esptool" signal.

---

## Current state (2026-06-02)

Verified working on cold boot: esptool recovery + FS reformat; interruptible boot
guard; WiFi connect; `camera_capture()` -> upload returns 200 and lands in the
gallery; touch detection with the camera active.

**OPEN ISSUE — booth not yet fully running on cold boot.** Manual screen draws
(`show_bg`/`draw_text`/`show_draw`) race with the camera's continuous DMA and
HANG on a fresh cold boot (hang point varies — draw, sleep, or tight loop = DMA
race). `screen.stop_camera()` tames it via `mpremote run` but not reliably on
cold boot. What works on cold boot: camera init + `screen.init()` + touch-poll +
capture + upload **as long as nothing is manually drawn.**

**NEXT STEP:** drive the UI with `screen.show_camera_feed(<ARG>)` — a live
viewfinder the firmware coordinates with the camera DMA — instead of manual
draws. It takes exactly ONE positional arg (the "takes 2 positional / 3 given"
error counts `self`); arg meaning still TBD (try a small int / dir / scale).
Build the booth UI around the feed (great "see yourself" UX) and drop the
draw-based screens (`show_ready`/`show_countdown`/`show_uploading`/`show_done`)
that cause the hang.

---

## Common commands

Always free the port first: `fuser -k /dev/ttyACM0; sleep 1`

**Recover a bricked / hung / corrupt-FS K10** (deterministic):
```bash
esptool --chip esp32s3 --port /dev/ttyACM0 flash-id        # confirm reachable
esptool --chip esp32s3 --port /dev/ttyACM0 erase-flash
esptool --chip esp32s3 --port /dev/ttyACM0 write-flash 0x0 ~/Downloads/k10_micropython_v0.9.8.bin
# Then reformat the FS from the REPL (write-flash leaves it "corrupted"):
#   paste-mode: import vfs, flashbdev; vfs.VfsLfs2.mkfs(flashbdev.bdev); vfs.mount(...)
# (see /k10 skill references/recovery.md for the exact paste-mode script)
```

**Deploy firmware** (device healthy / boot-guard reachable):
```bash
cp firmware/booth_config.example.py firmware/booth_config.py   # edit SSID/PASSWORD/SERVER_URL/CAM_ID
mpremote connect /dev/ttyACM0 cp firmware/photo_booth.py :main.py
mpremote connect /dev/ttyACM0 cp firmware/booth_config.py :booth_config.py
```
If a hung `main.py` blocks mpremote: hard-reset (`esptool ... --after hard-reset
flash-id`) then immediately retry `mpremote cp` to catch the 3s boot-guard window
(loop 3-4x).

**Cold-boot + watch serial** (don't send bytes during the 3s guard):
```bash
esptool --chip esp32s3 --port /dev/ttyACM0 --after hard-reset flash-id
python3 -c "import serial,time; s=serial.Serial('/dev/ttyACM0',115200,timeout=0.1); \
import time as t; e=t.time()+25; b=b''; \
[b:=b+(s.read(256) or b'') for _ in iter(lambda: t.time()<e, False)]; print(b.decode('utf-8','replace'))"
```

**Run the gallery server:**
```bash
cd server
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080
# Gallery: http://<server-ip>:8080/booth.html   Upload: /upload   List: /api/photos
```
Point each K10's `SERVER_URL` at that machine's LAN IP:8080.

---

## Capture pipeline

K10 `camera_capture()` -> 153600 B RGB565 -> POST `/upload` with headers
`Content-Type: image/x-rgb565`, `X-Width: 240`, `X-Height: 320`, `X-Cam-Id`,
`X-Event` -> server `decode_frame()` (`Image.frombuffer("RGB",(240,320),body,
"raw","BGR;16",0,1)`) -> `make_polaroid()` -> JPEG saved -> WebSocket broadcast
-> gallery prepends the new polaroid live.

## Conventions

- Co-author commits: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Never commit `booth_config.py` (WiFi password) — it is gitignored.
- The firmware is intended to be open-sourced; keep secrets out of tracked files.
