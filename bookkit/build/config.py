"""Settings from .env / environment. Mirrors the reel pipeline's variables so
one .env serves both projects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    pexels_api_key: str
    fal_key: str
    media_cooldown: float
    media_prefer: str  # "ai" | "stock" — ai uses Flux first for Indian interiors
    cache_dir: Path
    target_dpi: int
    max_book_mb: float
    trim_in: tuple[float, float]
    series_tagline: str
    closing_headline: str
    closing_blurb: str

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_pexels(self) -> bool:
        return bool(self.pexels_api_key)

    @property
    def has_fal(self) -> bool:
        return bool(self.fal_key)


def settings() -> Settings:
    load_dotenv()
    prefer = os.getenv("MEDIA_PREFER", "ai").strip().lower()
    if prefer not in {"ai", "stock"}:
        prefer = "ai"
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        pexels_api_key=os.getenv("PEXELS_API_KEY", ""),
        fal_key=os.getenv("FAL_KEY", ""),
        media_cooldown=float(os.getenv("MEDIA_COOLDOWN", "15")),
        media_prefer=prefer,
        cache_dir=Path(os.getenv("MEDIA_CACHE", ".cache/media")),
        target_dpi=int(os.getenv("TARGET_DPI", "150")),
        max_book_mb=float(os.getenv("MAX_BOOK_MB", "5")),
        trim_in=(6.0, 9.0),
        series_tagline=os.getenv(
            "SERIES_TAGLINE",
            "Measured layouts and calm surfaces for Indian apartments.",
        ).strip(),
        closing_headline=os.getenv("SERIES_CLOSING_HEADLINE", "Your next room starts here").strip(),
        closing_blurb=os.getenv(
            "SERIES_CLOSING_BLURB",
            "More room kits, declutter sprints, and measured layouts for Indian apartments — same series language, same clear numbers.",
        ).strip(),
    )
