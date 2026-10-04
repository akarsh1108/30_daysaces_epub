"""The BookPlan: everything the renderer needs, nothing about how it was made.

Both planners (heuristic, OpenAI) emit this. Media resolvers fill in paths.
The renderer only ever reads this. Keep it boring and serialisable.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

FigureKind = Literal["plan", "elevation", "comparison", "ratio_bar", "chart", "table", "photo"]
PhotoSource = Literal["pexels", "flux", "mock", "openai"]


class Photo(BaseModel):
    """A photograph slot. Resolved by the media stage."""

    query: str = Field(description="Stock-photo search query, concrete and visual")
    flux_prompt: str = Field(default="", description="Fallback generation prompt if stock fails")
    alt: str = ""
    source: Optional[PhotoSource] = None
    path: Optional[str] = None
    credit: str = ""  # "Photo by X on Pexels" or "AI-generated"
    ai_generated: bool = False  # drives the KDP disclosure line
    provider_id: str = ""  # "pexels:12345" — used to cap reuse of the same photo


class Figure(BaseModel):
    """A drawn diagram. Rendered deterministically to SVG."""

    kind: FigureKind
    title: str
    caption: str = ""
    values: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list, description="One label per value where useful")
    minimum: Optional[str] = Field(default=None, description="A value to draw in red as the floor")
    spec_id: Optional[str] = None
    path: Optional[str] = None  # filled by media stage


class Step(BaseModel):
    text: str
    minutes: Optional[int] = None


class Section(BaseModel):
    """One teachable unit. Renders as an opener spread plus body pages."""

    slug: str
    title: str
    day_label: str = ""  # "Day 4" / "Week 2"
    hero: Optional[Photo] = None
    scenario: str = Field(default="", description="3-5 sentences: a named person, a real room, a specific problem")
    principle: str = Field(default="", description="The rule in one sentence")
    body: list[str] = Field(default_factory=list, description="Paragraphs, kept from source")
    figures: list[Figure] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    steps_label: str = "Do this"
    mistake: str = ""
    checklist: list[str] = Field(default_factory=list)
    checklist_label: str = "Before you move on"
    inline_photos: list[Photo] = Field(default_factory=list)


class Chapter(BaseModel):
    slug: str
    title: str
    intro: str = ""
    opener: Optional[Photo] = None
    sections: list[Section] = Field(default_factory=list)


class BookPlan(BaseModel):
    slug: str
    title: str
    subtitle: str = ""
    tagline: str = ""  # cover line under the title
    author: str = ""
    series: str = ""
    closing_headline: str = ""
    closing_blurb: str = ""
    accent_hex: str = "#1E5F6B"
    units: Literal["imperial", "metric"] = "imperial"
    cover: Optional[Photo] = None  # shared cover slot; defaults to first opener photo
    designed_cover: bool = False  # full-bleed cover art; do not overlay the series panel
    chapters: list[Chapter] = Field(default_factory=list)

    @property
    def photo_slots(self) -> list[Photo]:
        """Every photo object in the plan (no dedupe). Used for resolve/stage."""
        out: list[Photo] = []
        if self.cover:
            out.append(self.cover)
        for ch in self.chapters:
            if ch.opener:
                out.append(ch.opener)
            for s in ch.sections:
                if s.hero:
                    out.append(s.hero)
                out.extend(s.inline_photos)
        return out

    @property
    def all_photos(self) -> list[Photo]:
        """Unique photos by resolved path (or query). For manifests / AI counts."""
        out: list[Photo] = []
        seen: set[str] = set()
        for p in self.photo_slots:
            key = p.path or f"q:{p.query}"
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        return out

    @property
    def all_figures(self) -> list[Figure]:
        return [f for ch in self.chapters for s in ch.sections for f in s.figures]

    @property
    def ai_image_count(self) -> int:
        return sum(1 for p in self.all_photos if p.ai_generated)
