"""Photo resolution: Pexels first, Flux (fal.ai) fallback, mock when offline.

Every fetched image is cached by provider id so re-running a build costs no
API calls. MEDIA_COOLDOWN (from the reel pipeline .env) is honoured between
live calls. Every image is downsampled to the target DPI at the widest it will
ever print, and re-encoded as JPEG, before it enters the book. Provenance is
recorded on the Photo so the manifest can drive the KDP AI-content disclosure.

The same photograph may appear at most MAX_PHOTO_USES times in one book —
callers pass already-capped identities in `banned`.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw

from ..config import Settings
from ..plan import Photo

# 6x9 trim, full bleed width incl. 0.125in bleed = 6.25in. At 150 DPI: 938px.
# Round up so a spread hero can also be used.
MAX_WIDTH_PX = 1000
JPEG_QUALITY = 82
MAX_PHOTO_USES = 3
OPENAI_IMAGE_MODELS = ("gpt-image-2", "gpt-image-1.5", "gpt-image-1")

_last_call = 0.0


def _cooldown(cfg: Settings) -> None:
    global _last_call
    wait = cfg.media_cooldown - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _finalise(raw: bytes, out: Path) -> Path:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if img.width > MAX_WIDTH_PX:
        ratio = MAX_WIDTH_PX / img.width
        img = img.resize((MAX_WIDTH_PX, int(img.height * ratio)), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return out


def identity(photo: Photo) -> str:
    """Stable id for reuse capping: provider id, else file hash, else query."""
    if photo.provider_id:
        return photo.provider_id
    if photo.path:
        p = Path(photo.path)
        if p.is_file():
            return "sha1:" + hashlib.sha1(p.read_bytes()).hexdigest()[:16]
    return f"q:{photo.query}|{photo.flux_prompt}"


# ---------------------------------------------------------------- pexels ---

def _search_pexels(query: str, cfg: Settings, pages: tuple[int, ...] = (1,)) -> list[dict]:
    import httpx

    cache = cfg.cache_dir / "pexels" / "search" / f"{_key(query)}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    hits: list[dict] = []
    seen: set[int] = set()
    for page in pages:
        _cooldown(cfg)
        r = httpx.get(
            "https://api.pexels.com/v1/search",
            params={
                "query": query,
                "per_page": 20,
                "page": page,
                "orientation": "landscape",
                "size": "large",
            },
            headers={"Authorization": cfg.pexels_api_key},
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json().get("photos") or []
        for hit in batch:
            hid = hit.get("id")
            if hid in seen:
                continue
            seen.add(hid)
            hits.append(hit)
        if len(batch) < 20:
            break

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(hits), encoding="utf-8")
    return hits


def _apply_pexels_hit(photo: Photo, cfg: Settings, hit: dict) -> bool:
    import httpx

    pid = str(hit["id"])
    out = cfg.cache_dir / "pexels" / "by-id" / f"{pid}.jpg"
    meta = out.with_suffix(".txt")
    if not out.exists():
        raw = httpx.get(hit["src"]["large2x"], timeout=60).content
        _finalise(raw, out)
        meta.write_text(f"Photo by {hit['photographer']} on Pexels", encoding="utf-8")
    credit = meta.read_text(encoding="utf-8") if meta.exists() else f"Photo by {hit['photographer']} on Pexels"
    photo.path = str(out)
    photo.credit = credit
    photo.source = "pexels"
    photo.ai_generated = False
    photo.provider_id = f"pexels:{pid}"
    return True


def pexels(photo: Photo, cfg: Settings, banned: set[str] | None = None) -> bool:
    banned = banned or set()
    queries = [photo.query]
    words = photo.query.split()
    if len(words) > 4:
        queries.append(" ".join(words[:5]))
    queries.append("indian apartment balcony plants daylight interior")

    tried: set[str] = set()
    for q in queries:
        if q in tried:
            continue
        tried.add(q)
        for hit in _search_pexels(q, cfg):
            pid = f"pexels:{hit['id']}"
            if pid in banned:
                continue
            return _apply_pexels_hit(photo, cfg, hit)
    return False


# ------------------------------------------------------------------ flux ---

_flux_disabled_reason: str | None = None


def flux(photo: Photo, cfg: Settings, banned: set[str] | None = None) -> bool:
    global _flux_disabled_reason
    import fal_client
    import httpx

    banned = banned or set()
    if _flux_disabled_reason:
        raise RuntimeError(_flux_disabled_reason)

    prompt = photo.flux_prompt or photo.query
    fid = f"flux:{_key(prompt)}"
    if fid in banned:
        return False
    out = cfg.cache_dir / "flux" / f"{_key(prompt)}.jpg"
    if out.exists():
        photo.path, photo.credit, photo.source, photo.ai_generated = str(out), "AI-generated image", "flux", True
        photo.provider_id = fid
        return True

    _cooldown(cfg)
    try:
        result = fal_client.subscribe(
            "fal-ai/flux/dev",
            arguments={"prompt": prompt, "image_size": "landscape_4_3", "num_images": 1,
                       "enable_safety_checker": True},
        )
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "Exhausted balance" in msg or "locked" in msg.lower():
            _flux_disabled_reason = msg
        raise
    url = result["images"][0]["url"]
    raw = httpx.get(url, timeout=120).content
    _finalise(raw, out)
    photo.path, photo.credit, photo.source, photo.ai_generated = str(out), "AI-generated image", "flux", True
    photo.provider_id = fid
    return True


# ---------------------------------------------------------------- openai ---

def openai_image(photo: Photo, cfg: Settings, banned: set[str] | None = None, variant: int = 0) -> bool:
    """Generate a unique still with the current GPT image model (gpt-image-2, then fallbacks)."""
    import base64

    from openai import OpenAI

    banned = banned or set()
    if not cfg.openai_api_key:
        return False

    base = (photo.flux_prompt or photo.query).strip()
    prompt = (
        f"{base}. Photorealistic photograph of a compact Indian apartment kitchen or cleaning setup, "
        f"natural window light, magazine styling, no people, no faces, no readable labels or logos, "
        f"no watermark, no text overlay. Unique variation {variant}: different camera angle, props, "
        f"and time of day from other images in the same book."
    )
    fid = f"openai:{_key(prompt)}"
    extra = variant
    while fid in banned:
        extra += 1
        prompt = prompt + f" seed-note {extra}"
        fid = f"openai:{_key(prompt)}"

    out = cfg.cache_dir / "openai" / f"{_key(prompt)}.jpg"
    if out.exists():
        photo.path = str(out)
        photo.credit = "AI-generated image"
        photo.source = "openai"
        photo.ai_generated = True
        photo.provider_id = fid
        return True

    client = OpenAI(api_key=cfg.openai_api_key)
    last_err: Exception | None = None
    raw: bytes | None = None
    for model in OPENAI_IMAGE_MODELS:
        _cooldown(cfg)
        try:
            result = client.images.generate(
                model=model,
                prompt=prompt,
                size="1536x1024",
                quality="medium",
                output_format="jpeg",
            )
            b64 = result.data[0].b64_json
            if not b64:
                raise RuntimeError(f"{model} returned no image data")
            raw = base64.b64decode(b64)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"  openai {model} failed: {e}")
            continue
    if raw is None:
        if last_err:
            raise last_err
        return False

    _finalise(raw, out)
    photo.path = str(out)
    photo.credit = "AI-generated image"
    photo.source = "openai"
    photo.ai_generated = True
    photo.provider_id = fid
    return True


# ------------------------------------------------------------------ mock ---

def mock(photo: Photo, cfg: Settings, banned: set[str] | None = None) -> bool:
    """Neutral placeholder carrying the query, so layout can be judged offline."""
    banned = banned or set()
    qk = _key(photo.query)
    for variant in range(16):
        pid = f"mock:{qk}:{variant}"
        if pid in banned:
            continue
        out = cfg.cache_dir / "mock" / f"{qk}-v{variant}.jpg"
        if not out.exists():
            img = Image.new("RGB", (1000, 700), (232, 239, 240))
            d = ImageDraw.Draw(img)
            d.rectangle([40, 40, 960, 660], outline=(30, 95, 107), width=4)
            d.text((60, 300), photo.query[:60], fill=(30, 95, 107))
            d.text((60, 340), f"placeholder v{variant} — Pexels/Flux in a live build", fill=(120, 130, 132))
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out, "JPEG", quality=70)
        photo.path, photo.credit, photo.source, photo.ai_generated = str(out), "", "mock", False
        photo.provider_id = pid
        return True
    return False


def resolve(photo: Photo, cfg: Settings, mode: str = "live", banned: set[str] | None = None) -> None:
    """mode: live (ai-first or stock-first) | mock.

    For Indian home-decor books, Flux usually beats generic Pexels stock.
    MEDIA_PREFER=ai (default) tries Flux first when FAL_KEY is set.
    `banned` is identities already at MAX_PHOTO_USES in this book.
    """
    banned = banned or set()
    if mode == "mock":
        mock(photo, cfg, banned=banned)
        return

    prefer_ai = cfg.media_prefer == "ai" and cfg.has_fal
    order = (("flux", "pexels") if prefer_ai else ("pexels", "flux"))

    for provider in order:
        try:
            if provider == "flux" and cfg.has_fal and flux(photo, cfg, banned=banned):
                return
            if provider == "pexels" and cfg.has_pexels and pexels(photo, cfg, banned=banned):
                return
        except Exception as e:  # noqa: BLE001 - a failed provider must not kill the build
            print(f"  {provider} failed for '{photo.query}': {e}")
    mock(photo, cfg, banned=banned)
