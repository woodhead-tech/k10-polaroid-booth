import asyncio
import io
import json
import os
import random
import time
from pathlib import Path
from typing import List

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from PIL import Image, ImageDraw, ImageFont

app = FastAPI()

PHOTOS_DIR = Path(__file__).parent / "photos"
PHOTOS_DIR.mkdir(exist_ok=True)

STATIC_DIR = Path(__file__).parent


# ── WebSocket hub ────────────────────────────────────────────────────────────

class Hub:
    def __init__(self):
        self.clients: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


hub = Hub()


# ── Polaroid effect ──────────────────────────────────────────────────────────

def _load_font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def make_polaroid(jpeg_bytes: bytes, caption: str) -> bytes:
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")

    # Center-crop to 3:4 portrait
    w, h = img.size
    target_ratio = 3 / 4
    if w / h > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))

    img = img.resize((600, 800), Image.LANCZOS)

    # White polaroid frame: 80px sides/top, 160px bottom
    canvas = Image.new("RGB", (760, 1040), "white")
    canvas.paste(img, (80, 80))

    draw = ImageDraw.Draw(canvas)
    font = _load_font(26)
    draw.text((380, 950), caption, fill="#888888", anchor="mm", font=font)

    # Slight random rotation
    angle = random.uniform(-4.5, 4.5)
    canvas = canvas.rotate(angle, fillcolor="white", expand=False)

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=88)
    return out.getvalue()


# ── Routes ───────────────────────────────────────────────────────────────────

@app.post("/upload")
async def upload(request: Request):
    jpeg_bytes = await request.body()
    cam_id = request.headers.get("X-Cam-Id", "cam0")
    event = request.headers.get("X-Event", "Ender · Class of 2026")

    ts = int(time.time() * 1000)
    filename = f"{ts}_{cam_id}.jpg"
    filepath = PHOTOS_DIR / filename

    polaroid = make_polaroid(jpeg_bytes, event)
    filepath.write_bytes(polaroid)

    await hub.broadcast({"photo": filename, "cam": cam_id})
    return {"ok": True, "filename": filename}


@app.get("/")
async def root():
    return RedirectResponse("/booth.html")


@app.get("/booth.html")
async def booth_page():
    return FileResponse(STATIC_DIR / "booth.html")


@app.get("/photos/{filename}")
async def get_photo(filename: str):
    filepath = PHOTOS_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        return HTMLResponse("not found", status_code=404)
    return FileResponse(filepath, media_type="image/jpeg")


@app.get("/api/photos")
async def list_photos():
    photos = sorted(PHOTOS_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
    return [p.name for p in photos]


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await hub.connect(ws)
    # Send all existing photos on connect (newest first so prepend = correct order)
    photos = sorted(PHOTOS_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    for photo in photos:
        try:
            await ws.send_json({"photo": photo.name, "cam": "existing", "existing": True})
        except Exception:
            hub.disconnect(ws)
            return
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)
