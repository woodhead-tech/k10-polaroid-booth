# K10 Server — Gallery Server Setup

FastAPI server that receives RGB565 frames from K10 cameras, applies a polaroid
frame, and serves a live WebSocket gallery.

## Location

Repo: `~/WORKSPACE/k10-polaroid-booth/server/`

## Setup (macOS)

```bash
cd ~/WORKSPACE/k10-polaroid-booth/server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Dependencies: `fastapi`, `uvicorn[standard]`, `pillow`, `websockets`, `python-multipart`

## Run

```bash
cd ~/WORKSPACE/k10-polaroid-booth/server
.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080
```

Get your LAN IP for K10 config:
```bash
# macOS:
ipconfig getifaddr en0    # WiFi
# or
ifconfig | grep "inet " | grep -v 127.0.0.1
```

Note: `run.sh` uses `hostname -I` which is Linux-only. On macOS use the above.

## Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/upload` | POST | Receive frame from K10 (RGB565 or JPEG) |
| `/booth.html` | GET | Live gallery page |
| `/photos/{filename}` | GET | Serve individual polaroid JPEG |
| `/api/photos` | GET | List all photos (JSON array of filenames) |
| `/ws` | WebSocket | Real-time photo notifications |
| `/` | GET | Redirect to `/booth.html` |

## Upload Headers (from K10)

```
Content-Type: image/x-rgb565
X-Width: 240
X-Height: 320
X-Cam-Id: cam1
X-Event: Ender's Party - June 2026
```

## RGB565 Decode Pipeline

```python
from PIL import Image

def decode_frame(body: bytes, content_type: str, width: int, height: int) -> Image.Image:
    if "rgb565" in content_type:
        # K10 camera_capture() returns raw BGR565 (little-endian)
        img = Image.frombuffer("RGB", (width, height), body, "raw", "BGR;16", 0, 1)
    else:
        img = Image.open(io.BytesIO(body))
    return img.convert("RGB")
```

153600 bytes = 240 * 320 * 2 bytes/pixel (RGB565).

## Polaroid Frame

`make_polaroid()` applies:
- Center-crop to 3:4 portrait
- Resize to 600x800
- White frame: 80px sides/top, 160px bottom (canvas 760x1040)
- Caption text centered in bottom margin
- Random rotation (-4.5 to +4.5 degrees)
- JPEG output at quality 88

## Gallery Page (`booth.html`)

- Navy/gold theme, masonry grid layout
- WebSocket auto-reconnect (3s retry)
- New photos slide in with animation
- Existing photos loaded on connect
- Responsive: 4 columns → 3 → 2 → 1

## Test Upload (curl, without a K10)

Generate a test frame and POST it:
```bash
# Create a dummy 153600-byte file (or use a real capture)
python3 -c "
from PIL import Image
import io
img = Image.new('RGB', (240, 320), color=(100, 150, 200))
# Convert to RGB565
raw = img.tobytes('raw', 'BGR;16')
open('/tmp/test_frame.rgb565', 'wb').write(raw)
print(len(raw), 'bytes')
"

curl -X POST http://localhost:8080/upload \
  -H "Content-Type: image/x-rgb565" \
  -H "X-Width: 240" -H "X-Height: 320" \
  -H "X-Cam-Id: test" -H "X-Event: Test Upload" \
  --data-binary @/tmp/test_frame.rgb565
```

## Photos Storage

Photos save to `server/photos/` (gitignored). Filename format: `{timestamp_ms}_{cam_id}.jpg`.
