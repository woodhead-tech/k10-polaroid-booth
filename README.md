# k10-polaroid-booth

A DIY live photo booth built on the [UniHiker K10](https://www.dfrobot.com/product-2925.html) — an ESP32-S3 microcontroller with a built-in camera, color display, and WiFi.

Press a button and the K10 captures a photo, applies a polaroid-style frame, and it appears in a live gallery shown on any screen at the venue in seconds.

## 🚀 Quick Start (Firmware)

1. **Prerequisites:** Install `mpremote` and `esptool`.
2. **Flash:** If your K10 is fresh or bricked, flash the MicroPython image (v0.9.8+).
3. **Configure:** Copy `firmware/booth_config.example.py` to `firmware/booth_config.py` and set your WiFi/Server details.
4. **Deploy:**
   ```bash
   mpremote connect /dev/ttyACM0 cp photo_booth.py :main.py
   mpremote connect /dev/ttyACM0 cp booth_config.py :booth_config.py
   ```

## 🖥️ Gallery Server

The server is built with FastAPI and Pillow. It handles raw RGB565 decoding from the K10 camera and generates the polaroid-style JPEGs.

**Note:** If you are using this in a production environment, use the server implementation found in the [graduation-site](https://github.com/woodhead-tech/graduation-site) repository (under `/booth`), which is the canonical version with optimized RGB565 support.

## 🛠️ Hardware Insights

The K10 is a powerful but nuanced board. Here are some hard-won facts:

- **Buttons:** The A/B face buttons are shared with the camera's parallel bus; they are **unusable** once the camera is initialized. Use the touch screen or the HOME button for triggers.
- **WiFi + Screen:** Initialize WiFi **before** calling `screen.init()`, or the display framebuffer will starve the WiFi DMA.
- **DMA Races:** Manual screen draws (`draw_text`, etc.) can race with the camera's continuous DMA and cause hangs on cold boot. Use the viewfinder API for the most stable UI.
- **Recovery:** If the device hangs, use `esptool` to erase and reflash. No physical boot button is required; the built-in USB-Serial-JTAG handles it.

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
