"""PDF -> Document extraction using PyMuPDF."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pymupdf

from .models import Block, Document, Heading

LIST_MARKER = re.compile(r"^\s*(?:[\u2022\u25cb\u25e6\u00b7\u2043\-\u2013]|\d+[.)])\s+")

# Ligature and typographic normalisation. PDF text layers frequently encode
# these as single glyphs, which breaks naive keyword matching downstream.
_NORMALISE = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\u2019": "'", "\u2018": "'", "\u201c": '"',
    "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u00a0": " ",
}


def normalise(text: str) -> str:
    for bad, good in _NORMALISE.items():
        text = text.replace(bad, good)
    return text


def _slug(path: Path) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")


def extract(pdf_path: str | Path) -> Document:
    path = Path(pdf_path)
    doc = pymupdf.open(path)

    blocks: list[Block] = []
    size_counter: Counter[float] = Counter()
    fonts: set[str] = set()
    images_by_page: dict[int, int] = {}
    text_parts: list[str] = []

    for pno, page in enumerate(doc, start=1):
        n_images = len(page.get_images())
        if n_images:
            images_by_page[pno] = n_images

        for blk in page.get_text("dict")["blocks"]:
            if blk.get("type") != 0:
                continue
            for line in blk.get("lines", []):
                line_text = "".join(s["text"] for s in line["spans"])
                line_text = normalise(line_text).strip()
                if not line_text:
                    continue
                spans = line["spans"]
                dominant = max(spans, key=lambda s: len(s["text"]))
                size = round(dominant["size"], 1)
                font = dominant["font"]
                size_counter[size] += len(line_text)
                fonts.add(font)
                blocks.append(
                    Block(
                        text=line_text,
                        page=pno,
                        size=size,
                        font=font,
                        bold="bold" in font.lower(),
                        is_list_item=bool(LIST_MARKER.match(line_text)),
                    )
                )
                text_parts.append(line_text)

    body_size = size_counter.most_common(1)[0][0] if size_counter else 0.0

    # Any size meaningfully larger than body text is a heading candidate.
    heading_sizes = sorted({b.size for b in blocks if b.size > body_size + 0.5}, reverse=True)
    level_of = {s: i + 1 for i, s in enumerate(heading_sizes)}

    headings = _merge_wrapped_headings(
        [
            Heading(text=b.text, page=b.page, size=b.size, level=level_of[b.size])
            for b in blocks
            if b.size in level_of
        ]
    )

    outline = doc.get_toc()
    full_text = "\n".join(text_parts)

    return Document(
        path=str(path),
        slug=_slug(path),
        page_count=doc.page_count,
        page_width_pt=doc[0].rect.width,
        page_height_pt=doc[0].rect.height,
        word_count=len(full_text.split()),
        fonts=sorted(fonts),
        image_count=sum(images_by_page.values()),
        images_by_page=images_by_page,
        has_outline=bool(outline),
        outline_entries=len(outline),
        headings=headings,
        blocks=blocks,
        size_histogram=dict(size_counter),
        full_text=full_text,
    )


def _merge_wrapped_headings(headings: list[Heading]) -> list[Heading]:
    """A heading that wraps to a second line arrives as two Heading records.

    Merge consecutive same-page, same-size headings so titles read whole.
    """
    merged: list[Heading] = []
    for h in headings:
        if (
            merged
            and merged[-1].page == h.page
            and merged[-1].size == h.size
            and not merged[-1].text.endswith((".", "?", "!"))
        ):
            merged[-1] = Heading(
                text=f"{merged[-1].text} {h.text}".strip(),
                page=merged[-1].page,
                size=merged[-1].size,
                level=merged[-1].level,
            )
        else:
            merged.append(h)
    return merged
