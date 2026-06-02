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

# -- AI style processing (optional — set GEMINI_API_KEY to enable) --
STYLE_ENABLED = False
STYLE_PROMPT = os.getenv(
    "BOOTH_STYLE_PROMPT",
    "Transform this photo into a vintage instant-film style with warm tones, "
    "slight vignette, and soft grain. Keep the subjects and composition identical."
)

try:
    from google import genai
    from google.genai import types
    from dotenv import load_dotenv
    load_dotenv()
    _api_key = os.getenv("GEMINI_API_KEY")
    if _api_key:
        _genai_client = genai.Client(api_key=_api_key)
        STYLE_ENABLED = True
        print(f"[style] AI style processing enabled (prompt: {STYLE_PROMPT[:60]}...)")
    else:
        print("[style] GEMINI_API_KEY not set — AI style disabled, using polaroid frame only")
except ImportError:
    print("[style] google-genai not installed — AI style disabled")


# -- WebSocket hub --

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


# -- Polaroid effect --

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


def decode_frame(body: bytes, content_type: str, width: int, height: int) -> Image.Image:
    """Accept JPEG or raw RGB565 bytes from K10, return PIL Image."""
    if "rgb565" in content_type:
        img = Image.frombuffer("RGB", (width, height), body, "raw", "BGR;16", 0, 1)
    else:
        img = Image.open(io.BytesIO(body))
    return img.convert("RGB")


def make_polaroid(img: Image.Image, caption: str) -> bytes:
    """Apply polaroid frame to a PIL Image, return JPEG bytes."""
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


async def apply_ai_style(img: Image.Image) -> Image.Image | None:
    """Run image through Gemini for style transfer. Returns styled image or None on failure."""
    if not STYLE_ENABLED:
        return None
    try:
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="JPEG", quality=90)
        img_bytes.seek(0)

        image_part = types.Part.from_bytes(data=img_bytes.getvalue(), mime_type="image/jpeg")

        response = await asyncio.to_thread(
            _genai_client.models.generate_content,
            model="gemini-2.0-flash-exp",
            contents=[STYLE_PROMPT, image_part],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        # Extract generated image from response
        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data and part.inline_data.mime_type.startswith("image/"):
                return Image.open(io.BytesIO(part.inline_data.data)).convert("RGB")

        print("[style] No image in Gemini response — using original")
        return None
    except Exception as e:
        print(f"[style] AI processing failed: {e} — using original")
        return None


# -- Routes --

@app.post("/upload")
async def upload(request: Request):
    body = await request.body()
    content_type = request.headers.get("content-type", "image/jpeg")
    cam_id = request.headers.get("X-Cam-Id", "cam0")
    event = request.headers.get("X-Event", "Ender - Class of 2026")
    width = int(request.headers.get("X-Width", "240"))
    height = int(request.headers.get("X-Height", "320"))

    ts = int(time.time() * 1000)
    filename = f"{ts}_{cam_id}.jpg"
    filepath = PHOTOS_DIR / filename

    # Decode the raw frame
    img = decode_frame(body, content_type, width, height)

    # Apply AI style if enabled (non-blocking — falls back to original on failure)
    styled_img = await apply_ai_style(img)
    final_img = styled_img if styled_img is not None else img

    # Apply polaroid frame
    polaroid = make_polaroid(final_img, event)
    filepath.write_bytes(polaroid)

    # Also save the raw original (no frame) for reference
    raw_path = PHOTOS_DIR / f"{ts}_{cam_id}_raw.jpg"
    raw_buf = io.BytesIO()
    img.save(raw_buf, "JPEG", quality=92)
    raw_path.write_bytes(raw_buf.getvalue())

    await hub.broadcast({"photo": filename, "cam": cam_id})
    return {"ok": True, "filename": filename, "styled": styled_img is not None}


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
    # Only return polaroid frames (exclude _raw files)
    photos = sorted(
        [p for p in PHOTOS_DIR.glob("*.jpg") if "_raw" not in p.name],
        key=lambda p: p.stat().st_mtime,
    )
    return [p.name for p in photos]


@app.get("/api/style")
async def get_style():
    """Check AI style status and current prompt."""
    return {"enabled": STYLE_ENABLED, "prompt": STYLE_PROMPT}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await hub.connect(ws)
    photos = sorted(
        [p for p in PHOTOS_DIR.glob("*.jpg") if "_raw" not in p.name],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
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
