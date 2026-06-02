# K10 Polaroid Photo Booth

A fleet of K10s act as wireless polaroid cameras. Press the HOME button → capture
→ POST the frame to a server → server applies a polaroid frame + caption → photos
appear live on a shared gallery web page. Built for Ender's graduation (ceremony
2026-06-13, party 2026-06-27).

## Components

| Part | Location | Role |
|------|----------|------|
| Firmware | `~/Workspace/k10-polaroid-booth/firmware/photo_booth.py` | runs as `main.py` on each K10 |
| Per-device config | `~/Workspace/k10-polaroid-booth/firmware/booth_config.py` | SSID/PASSWORD/SERVER_URL/CAM_ID/EVENT |
| Server (canonical) | `~/Workspace/graduation-site/booth/server.py` | FastAPI; decodes RGB565, makes polaroid, live gallery via WebSocket |
| Gallery page | `~/Workspace/graduation-site/booth/booth.html` | navy/gold masonry wall, auto-updates |
| Case (3D print) | `~/Downloads/aeba99b6e646d17aecab6ccfb3ca739f.zip` | K10CaseTop.stl + K10CaseBottom.stl — print on Ender 3 |

There is a **stale duplicate** server at
`~/Workspace/k10-polaroid-booth/server/server.py` that does NOT decode RGB565
(it calls `Image.open()` on raw bytes and crashes). Use the `graduation-site`
server. Consider deleting/replacing the duplicate.

## Capture → server pipeline

1. K10 `cam.camera_capture()` → 153600 bytes raw RGB565 (240×320)
2. POST to `SERVER_URL/upload` with headers:
   `Content-Type: image/x-rgb565`, `X-Width: 240`, `X-Height: 320`,
   `X-Cam-Id: camN`, `X-Event: <caption>`
3. Server `decode_frame()`:
   `Image.frombuffer("RGB", (240,320), body, "raw", "BGR;16", 0, 1)`
4. Server `make_polaroid()` → center-crop 3:4, white frame, caption, slight random
   rotation → JPEG saved to `photos/`
5. WebSocket broadcast → gallery page prepends the new polaroid live

Controls: short press (<600ms) = instant capture; long press (≥600ms) = 3-2-1
countdown then capture.

## Run the server

```bash
cd ~/Workspace/graduation-site/booth
python3 -m venv .venv
.venv/bin/pip install fastapi "uvicorn[standard]" pillow websockets python-multipart
.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080
# Gallery: http://<server-ip>:8080/booth.html   Upload: /upload   List: /api/photos
```

## Deploy firmware to a K10

Device must be healthy (recover first if not — see recovery.md). Set `CAM_ID`
per unit (`cam1`…`camN`) and point `SERVER_URL` at the server's LAN IP.

```bash
cd ~/Workspace/k10-polaroid-booth/firmware
fuser -k /dev/ttyACM0; sleep 1
# patch CAM_ID / SERVER_URL into a temp config, then:
mpremote connect /dev/ttyACM0 cp photo_booth.py :main.py
mpremote connect /dev/ttyACM0 cp /tmp/booth_config_camN.py :booth_config.py
```

## Status (as of 2026-06-02)

One K10 (`cam1`, MAC `1c:db:d4:ac:be:68`) reflashed clean. Firmware reworked to a
synchronous, touch-triggered design. Committed to the repo (`main`, local — not
pushed). The live `firmware/booth_config.py` is now gitignored (held the WiFi
password); `booth_config.example.py` is the tracked template.

**Verified working (each piece, on cold boot unless noted):**
- esptool recovery + FS reformat (see recovery.md).
- Interruptible boot guard: `select.poll(sys.stdin)` for 3s. mpremote's own
  handshake bytes trip it, so the device is recoverable via mpremote — no reflash.
  (`utime.sleep(3)` is NOT a usable guard on USB-Serial-JTAG.)
- WiFi connect (must be BEFORE `screen.init()` — display framebuffer starves WiFi DMA).
- `camera_capture()` -> 153600 bytes RGB565; upload to server returns 200, photo
  lands in the gallery.
- Touch detection via FT6336 I2C: `k10_i2c.readfrom_mem(0x38, 0x02, 1)[0] & 0x0F > 0`.
  Works with the camera active. (The A/B buttons can't — they share GPIO5/11 with
  the camera's parallel bus. `button(button.a)._pin == 5`, `button.b` == 11.)
- Empirically disproved: the non-ASCII `·` in EVENT does NOT fault draw_text.

**OPEN — booth not yet fully running on cold boot:**
- Manual screen draws (`show_bg`/`draw_text`/`show_draw`) race with the camera's
  continuous DMA and HANG on a fresh cold boot. The hang point varies (a draw, a
  `utime.sleep_ms`, even a tight print loop) — classic DMA race.
- `screen.stop_camera()` tames it when reached via `mpremote run` (soft reset
  leaves the camera in a calmer state) but NOT reliably on a fresh cold boot.
- What DOES work on cold boot: camera init + `screen.init()` + touch-poll loop +
  `camera_capture()` + upload — **as long as nothing is manually drawn.**
- **NEXT STEP:** drive the UI with `screen.show_camera_feed(<ARG>)` — a live
  viewfinder the firmware coordinates with the camera DMA — instead of manual
  draws. `show_camera_feed` takes exactly ONE positional arg (the "takes 2
  positional / 3 given" error counts `self`); arg meaning still TBD (try a small
  int / dir / scale). Build the booth UI around the feed (great "see yourself"
  UX) and avoid manual draws entirely. The draw-based screens in photo_booth.py
  (`show_ready`/`show_countdown`/`show_uploading`/`show_done`) are the hang source.

**Debug workflow that works:** deploy a marker-instrumented build, hard-reset via
`esptool --after hard-reset flash-id`, read serial for ~22s. To deploy when a bad
main.py is loaded: the boot guard lets mpremote in, but if a hung main.py blocks
it, hard-reset then retry `mpremote cp` to catch the guard window (loop 3-4x).

## Fleet / business

Tracked in Kanboard project 8 (Polaroid Booth Business), column 47. The longer
term idea is renting polaroid-booth K10 setups for events; open-sourcing the
firmware as a marketing funnel. Tasks: define rental package, build 5-6 unit
fleet, firmware v2 (self-discovery camera API + OTA), gallery v2 (per-event
branding), ESP32 venue displays, pricing/booking, open-source release.
