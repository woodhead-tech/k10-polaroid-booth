# k10-polaroid-booth

A DIY live photo booth built on the [UniHiker K10](https://www.dfrobot.com/product-2925.html) — an ESP32-S3 microcontroller with a built-in camera, color display, and WiFi.

Press a button and the K10 captures a photo, applies a polaroid-style frame, and it appears in a live gallery shown on any screen at the venue in seconds.

## How it works

```
[K10 cameras] --WiFi--> [Gallery server (laptop)] --WebSocket--> [Browser / TV / monitor]
  short press = instant shot     FastAPI + Pillow                  live polaroid grid
  long press  = 3-2-1 countdown  applies polaroid frame
```

- **Short press** — instant capture and upload
- **Long press (≥600ms)** — 3-2-1 countdown on the K10 screen, then capture
- **Gallery** — photos appear in real time on any browser connected to the same network

## What's in this repo

```
firmware/
  photo_booth.py      MicroPython firmware for the K10
  booth_config.py     Venue config (WiFi SSID, server IP, camera ID, event label)
  upload_booth.sh     Flash firmware to a K10 over WiFi (no cable needed after first flash)

server/
  server.py           FastAPI gallery server (upload endpoint + WebSocket hub)
  booth.html          Live gallery page (WebSocket, polaroid grid, slide-in animation)
  requirements.txt    Python dependencies
  run.sh              Start the server (prints your local IP automatically)
```

## Requirements

**Hardware:**
- 1–6 UniHiker K10 boards (~$35 each from DFRobot)
- A laptop to run the gallery server
- A phone hotspot (or venue WiFi) — all devices on the same network

**Software:**
- Python 3.10+ on the laptop
- MicroPython on the K10 (comes pre-installed)

## Setup

### 1. Install server dependencies

```bash
cd server
pip install -r requirements.txt
```

### 2. Configure the venue

Edit `firmware/booth_config.py`:

```python
SSID      = "YourHotspot"
PASSWORD  = "YourPassword"
SERVER_URL = "http://LAPTOP_IP:8080"   # fill in after step 3
CAM_ID    = "cam1"                      # unique per K10: cam1, cam2, ...
EVENT     = "Your Event · 2026"         # printed on each polaroid
```

### 3. Start the server

```bash
cd server
./run.sh
```

The script prints your local IP — copy it into `SERVER_URL` in `booth_config.py`.

### 4. Flash the K10s

Connect each K10 via USB-C, then:

```bash
cd firmware
CAM_ID=cam1 ./upload_booth.sh   # repeat with cam2, cam3, etc.
```

The script prompts you to unplug and replug the USB-C, then automatically downloads
the firmware over WiFi during the 3-second boot window.

### 5. Open the gallery

Navigate to `http://LAPTOP_IP:8080/booth.html` on any browser — laptop, smart TV,
tablet, or phone. Photos appear as they're taken.

## Camera API note

The K10 camera capture API varies by firmware version. `photo_booth.py` automatically
probes `cam.capture()` and `cam.snapshot()` on first boot and prints which one works
to the serial console. No manual configuration needed.

## Customizing the polaroid frame

Edit `make_polaroid()` in `server/server.py`:

- **Caption text** — controlled by the `EVENT` header sent from each K10
- **Frame size** — adjust the canvas dimensions and paste offset
- **Rotation range** — `random.uniform(-4.5, 4.5)` degrees by default

## Running multiple events

Keep two `booth_config.py` presets commented out and swap before each event:

```python
# Event 1
# SSID = "HotspotA"; EVENT = "Graduation · June 2026"

# Event 2
SSID = "HotspotB"; EVENT = "Wedding · August 2026"
```

## License

MIT
