import asyncio
import datetime
import io
import json
import os
import random
import time
from pathlib import Path
from typing import List

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from PIL import Image, ImageDraw, ImageFont

app = FastAPI()

PHOTOS_DIR = Path(__file__).parent / "photos"
PHOTOS_DIR.mkdir(exist_ok=True)

STATIC_DIR = Path(__file__).parent

# Phone photos can be 8MP+; cap before polaroid processing to keep things fast
MAX_UPLOAD_DIM = 2000

# Cam IDs that are placeholders, not real names
_DEFAULT_CAM_IDS = {"cam0", "cam1", "cam2", "guest", ""}

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


# -- Immich integration --

IMMICH_URL        = os.getenv("IMMICH_URL", "http://192.168.86.55:2283").rstrip("/")
IMMICH_API_KEY    = os.getenv("IMMICH_API_KEY", "")
IMMICH_ALBUM_PFX  = os.getenv("IMMICH_ALBUM_PREFIX", "Booth")
IMMICH_DISK_WRITE = os.getenv("IMMICH_DISK_WRITE", "true").lower() == "true"
IMMICH_SLIDESHOW  = os.getenv("IMMICH_SLIDESHOW", "false").lower() == "true"
IMMICH_ENABLED    = bool(IMMICH_API_KEY)

if IMMICH_ENABLED:
    print(f"[immich] enabled → {IMMICH_URL}  disk_write={IMMICH_DISK_WRITE}  slideshow={IMMICH_SLIDESHOW}")
else:
    print("[immich] disabled (IMMICH_API_KEY not set)")


class ImmichClient:
    """Thin async client for Immich v2 REST API."""

    def __init__(self, base_url: str, api_key: str):
        self._base = base_url
        self._headers = {"x-api-key": api_key, "Accept": "application/json"}
        # album_name -> album_id, populated lazily and cached for process lifetime
        self._album_cache: dict[str, str] = {}

    async def upload_asset(
        self,
        image_bytes: bytes,
        filename: str,
        device_asset_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        """Upload JPEG to Immich. Returns asset ID. 200 with duplicate:true is fine."""
        now_iso = (
            created_at
            or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        )
        asset_id = device_asset_id or filename
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self._base}/api/assets",
                headers=self._headers,
                files={"assetData": (filename, image_bytes, "image/jpeg")},
                data={
                    "deviceAssetId":  asset_id,
                    "deviceId":       "booth-app",
                    "fileCreatedAt":  now_iso,
                    "fileModifiedAt": now_iso,
                },
            )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"[immich] upload failed {r.status_code}: {r.text[:200]}")
        return r.json()["id"]

    async def get_or_create_album(self, album_name: str) -> str:
        """Return album ID, creating it if not found. Result is cached in-memory."""
        if album_name in self._album_cache:
            return self._album_cache[album_name]

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self._base}/api/albums",
                headers=self._headers,
                params={"shared": "false"},
            )
        if r.status_code != 200:
            raise RuntimeError(f"[immich] list albums failed {r.status_code}")

        for album in r.json():
            if album.get("albumName") == album_name:
                self._album_cache[album_name] = album["id"]
                print(f"[immich] found existing album '{album_name}' → {album['id']}")
                return album["id"]

        # Not found — create it
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{self._base}/api/albums",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"albumName": album_name},
            )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"[immich] create album failed {r.status_code}")
        album_id = r.json()["id"]
        self._album_cache[album_name] = album_id
        print(f"[immich] created album '{album_name}' → {album_id}")
        return album_id

    async def add_to_album(self, album_id: str, asset_ids: list[str]) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(
                f"{self._base}/api/albums/{album_id}/assets",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"ids": asset_ids},
            )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"[immich] add to album failed {r.status_code}")

    async def list_album_assets(self, album_name: str) -> list[dict]:
        """Return asset list for the slideshow proxy. Returns [] on any error."""
        try:
            album_id = await self.get_or_create_album(album_name)
        except Exception:
            return []
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{self._base}/api/albums/{album_id}", headers=self._headers)
        if r.status_code != 200:
            return []
        return r.json().get("assets", [])

    async def get_asset_preview(self, asset_id: str) -> bytes:
        """Fetch full-size JPEG preview bytes for the proxy route."""
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{self._base}/api/assets/{asset_id}/thumbnail",
                headers=self._headers,
                params={"size": "preview"},
            )
        r.raise_for_status()
        return r.content


# Singleton — only created when IMMICH_API_KEY is set
immich: ImmichClient | None = (
    ImmichClient(IMMICH_URL, IMMICH_API_KEY) if IMMICH_ENABLED else None
)


def _today_album_name() -> str:
    return f"{IMMICH_ALBUM_PFX} - {datetime.date.today().isoformat()}"


async def _upload_to_immich(image_bytes: bytes, filename: str, album_name: str) -> str | None:
    """Upload bytes to Immich and add to the event album. Fire-and-forget safe."""
    if not immich:
        return None
    try:
        asset_id = await immich.upload_asset(image_bytes, filename)
        album_id = await immich.get_or_create_album(album_name)
        await immich.add_to_album(album_id, [asset_id])
        print(f"[immich] uploaded {filename} → asset {asset_id}")
        return asset_id
    except Exception as e:
        print(f"[immich] upload error for {filename}: {e}")
        return None


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


def apply_vintage_filter(img: Image.Image) -> Image.Image:
    """Apply a vintage instant-film look: warm tones, subtle vignette, film grain."""
    from PIL import ImageFilter, ImageEnhance

    img = img.copy()
    w, h = img.size

    # Warm color shift (boost reds/yellows, reduce blues)
    r, g, b = img.split()
    r = r.point(lambda x: min(255, int(x * 1.08)))
    g = g.point(lambda x: min(255, int(x * 1.02)))
    b = b.point(lambda x: int(x * 0.88))
    img = Image.merge("RGB", (r, g, b))

    # Slight contrast reduction + warmth (mimics film)
    img = ImageEnhance.Contrast(img).enhance(0.92)
    img = ImageEnhance.Color(img).enhance(0.9)
    img = ImageEnhance.Brightness(img).enhance(1.03)

    # Subtle vignette — radial gradient mask (white center fading to dark edges)
    import math
    vignette = Image.new("L", (w, h), 255)
    cx, cy = w / 2, h / 2
    max_dist = math.sqrt(cx * cx + cy * cy)
    pixels = vignette.load()
    for y in range(h):
        for x in range(w):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            # Start darkening at 60% of max distance, darken to 70% brightness at edges
            factor = max(0, (dist / max_dist - 0.6) / 0.4)
            pixels[x, y] = int(255 * (1 - factor * 0.3))
    img = Image.composite(img, Image.new("RGB", (w, h), (15, 10, 5)), vignette)

    # Film grain overlay
    grain = Image.effect_noise((w, h), 20).convert("RGB")
    img = Image.blend(img, grain, 0.04)

    return img


async def apply_ai_style(img: Image.Image) -> Image.Image | None:
    """Run image through Gemini for AI style transfer, or Pillow vintage filter as fallback."""
    if STYLE_ENABLED:
        try:
            img_bytes = io.BytesIO()
            img.save(img_bytes, format="JPEG", quality=90)
            img_bytes.seek(0)

            image_part = types.Part.from_bytes(data=img_bytes.getvalue(), mime_type="image/jpeg")

            response = await asyncio.to_thread(
                _genai_client.models.generate_content,
                model="gemini-2.5-flash-image",
                contents=[STYLE_PROMPT, image_part],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )

            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    print("[style] AI style applied via Gemini")
                    return Image.open(io.BytesIO(part.inline_data.data)).convert("RGB")

            print("[style] No image in Gemini response — falling back to vintage filter")
        except Exception as e:
            print(f"[style] Gemini failed: {type(e).__name__} — using vintage filter")

    # Pillow vintage filter (always available, instant)
    return apply_vintage_filter(img)


# -- Routes --

@app.post("/upload")
async def upload(request: Request):
    body = await request.body()
    content_type = request.headers.get("content-type", "image/jpeg")
    cam_id = request.headers.get("X-Cam-Id", "cam0")
    event = request.headers.get("X-Event", "Ender's Graduation - June 2026")
    # Fix encoding: K10 sends raw UTF-8 bytes which HTTP headers interpret as Latin-1
    try:
        event = event.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    width = int(request.headers.get("X-Width", "240"))
    height = int(request.headers.get("X-Height", "320"))

    ts = int(time.time() * 1000)
    filename = f"{ts}_{cam_id}.jpg"
    filepath = PHOTOS_DIR / filename
    album_name = _today_album_name()

    # Decode the raw frame
    img = decode_frame(body, content_type, width, height)

    # Resize phone photos — polaroid only needs 600px, no need to process 4K images
    if max(img.size) > MAX_UPLOAD_DIM:
        img.thumbnail((MAX_UPLOAD_DIM, MAX_UPLOAD_DIM), Image.LANCZOS)

    # Use uploader's name as the polaroid caption when it looks like a real name
    caption = cam_id if cam_id not in _DEFAULT_CAM_IDS else event

    # -- raw variant --
    raw_filename = f"{ts}_{cam_id}_raw.jpg"
    raw_buf = io.BytesIO()
    img.save(raw_buf, "JPEG", quality=92)
    raw_bytes = raw_buf.getvalue()

    if IMMICH_DISK_WRITE:
        (PHOTOS_DIR / raw_filename).write_bytes(raw_bytes)

    # -- clean polaroid (immediate; no AI style) --
    clean_polaroid = make_polaroid(img, caption)
    clean_filename = f"{ts}_{cam_id}_clean.jpg"

    if IMMICH_DISK_WRITE:
        (PHOTOS_DIR / clean_filename).write_bytes(clean_polaroid)
        filepath.write_bytes(clean_polaroid)

    # Upload both variants to Immich (non-blocking; errors logged, never raised)
    asyncio.create_task(_upload_to_immich(raw_bytes,      raw_filename,   album_name))
    asyncio.create_task(_upload_to_immich(clean_polaroid, clean_filename, album_name))

    await hub.broadcast({"photo": filename, "cam": cam_id})

    # Apply AI style in background — overwrites the gallery file and uploads styled version
    asyncio.create_task(_style_and_replace(img, caption, filepath, filename, cam_id, album_name))

    return {"ok": True, "filename": filename}


async def _style_and_replace(
    img: Image.Image,
    event: str,
    filepath: Path,
    filename: str,
    cam_id: str,
    album_name: str,
):
    """Background task: apply AI style, write to disk/Immich, notify gallery."""
    try:
        styled_img = await apply_ai_style(img)
        if styled_img is not None:
            polaroid = make_polaroid(styled_img, event)

            if IMMICH_DISK_WRITE:
                filepath.write_bytes(polaroid)

            # Upload the AI-styled polaroid as the canonical gallery asset
            asyncio.create_task(_upload_to_immich(polaroid, filename, album_name))

            await hub.broadcast({"photo": filename, "cam": cam_id, "updated": True})
    except Exception as e:
        print(f"[style-bg] error: {e}")


@app.get("/")
async def root():
    return RedirectResponse("/upload.html")


@app.get("/upload.html")
async def upload_page():
    return FileResponse(STATIC_DIR / "upload.html")


@app.get("/booth.html")
async def booth_page():
    return FileResponse(STATIC_DIR / "booth.html")


@app.get("/slideshow.html")
async def slideshow_page():
    return FileResponse(STATIC_DIR / "slideshow.html")


@app.get("/photos/{filename}")
async def get_photo(filename: str):
    filepath = PHOTOS_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        return HTMLResponse("not found", status_code=404)
    return FileResponse(filepath, media_type="image/jpeg")


@app.get("/api/photos")
async def list_photos():
    # Only return styled polaroids (exclude _raw and _clean backups)
    photos = sorted(
        [p for p in PHOTOS_DIR.glob("*.jpg") if "_raw" not in p.name and "_clean" not in p.name],
        key=lambda p: p.stat().st_mtime,
    )
    return [p.name for p in photos]


@app.get("/api/album")
async def get_album():
    """Slideshow proxy: returns Immich album asset list with server-side auth injection."""
    if not immich:
        return {"album_ok": False, "photos": []}
    album_name = _today_album_name()
    try:
        assets = await immich.list_album_assets(album_name)
    except Exception as e:
        print(f"[immich] list_album_assets error: {e}")
        return {"album_ok": False, "photos": []}

    photos = []
    for a in assets:
        fname = a.get("originalFileName", "")
        # Slideshow only shows styled polaroids; raw and clean are Immich archive variants
        if "_raw" in fname or "_clean" in fname:
            continue
        try:
            ts = int(
                datetime.datetime.fromisoformat(
                    a["fileCreatedAt"].replace("Z", "+00:00")
                ).timestamp() * 1000
            )
        except Exception:
            ts = 0
        photos.append({
            "id":        a["id"],
            "url":       f"/api/photo-proxy/{a['id']}",
            "timestamp": ts,
            "caption":   a.get("exifInfo", {}).get("description", ""),
        })

    photos.sort(key=lambda p: p["timestamp"])
    return {"album_ok": True, "photos": photos}


@app.get("/api/photo-proxy/{asset_id}")
async def photo_proxy(asset_id: str):
    """Server-side proxy for Immich thumbnails — keeps API key off the browser."""
    if not immich:
        return HTMLResponse("Immich not configured", status_code=503)
    try:
        data = await immich.get_asset_preview(asset_id)
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={"Cache-Control": "max-age=3600"},
        )
    except Exception as e:
        print(f"[immich] photo-proxy error for {asset_id}: {e}")
        return HTMLResponse("proxy error", status_code=502)


@app.get("/api/style")
async def get_style():
    """Check AI style status and current prompt."""
    return {"enabled": STYLE_ENABLED, "prompt": STYLE_PROMPT}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await hub.connect(ws)
    photos = sorted(
        [p for p in PHOTOS_DIR.glob("*.jpg") if "_raw" not in p.name and "_clean" not in p.name],
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
