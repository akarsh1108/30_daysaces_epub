"""Offline planner. Splits the source into sections using the author's own
recurring markers, attaches audited diagram specs by page, and generates
neutral photo queries from the section title.

It cannot write scenarios or rewrite prose. That's the OpenAI planner's job.
This one exists so the whole pipeline runs, end to end, with no keys.
"""

from __future__ import annotations

import re

from ...models import AuditReport, Block, Heading
from ..plan import BookPlan, Chapter, Figure, Photo, Section, Step

SECTION_MARKERS = re.compile(
    r"^(The Problem|The Solution Framework|Deep Dive|Step[- ]By[- ]Step|Examples?|"
    r"Common Mistakes?|Key Takeaways?|Action Items?|Safety Notes?|Summary)\b",
    re.I,
)
STEP_LINE = re.compile(r"^\s*(?:[\u2022\u25cb\u25e6\-\u2013]|\d+[.)])\s+(.+)")
SKIP_CHAPTER = re.compile(r"^(introduction|conclusion|about the author|acknowledgements?)$", re.I)

ROOM_WORDS = re.compile(
    r"\b(living room|bedroom|kitchen|balcony|bathroom|wardrobe|dining|"
    r"paint|palette|colour|color|wall|accent|neutral|sample)\b",
    re.I,
)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _chapter_bounds(report: AuditReport) -> list[tuple[Heading, int, int]]:
    doc = report.document
    h1s = [h for h in doc.headings if h.level == 1]
    out = []
    for i, h in enumerate(h1s):
        end = h1s[i + 1].page - 1 if i + 1 < len(h1s) else doc.page_count
        out.append((h, h.page, end))
    return out


def _body_size(report: AuditReport) -> float:
    hist = report.document.size_histogram
    return max(hist, key=hist.get) if hist else 12.0


def _is_heading(b: Block, body_size: float) -> bool:
    """True for display text above the dominant body size (works for 18pt H1s)."""
    return b.size >= body_size + 1.5


def _blocks_in(report: AuditReport, start: int, end: int, heading_text: str = "") -> list[Block]:
    """Blocks of a chapter. Starts *after* the heading line on its page, so the tail
    of the previous chapter that shares the page is excluded."""
    body_size = _body_size(report)
    blocks = report.document.blocks
    first_word = heading_text.split()[0].lower() if heading_text else None
    started = first_word is None
    out: list[Block] = []
    for b in blocks:
        if b.page < start or b.page > end:
            continue
        if not started:
            if (
                b.page == start
                and _is_heading(b, body_size)
                and b.text.lower().startswith(first_word)
            ):
                started = True
            continue
        if _is_heading(b, body_size) and out:
            break
        if not _is_heading(b, body_size):
            out.append(b)
    if not out:
        out = [
            b
            for b in blocks
            if start <= b.page <= end and not _is_heading(b, body_size)
        ]
    return out


def _paragraphs(blocks: list[Block]) -> list[str]:
    """Rejoin PDF lines into paragraphs; a marker line or list item starts a new one."""
    paras: list[str] = []
    buf: list[str] = []
    for b in blocks:
        t = b.text.strip()
        if SECTION_MARKERS.match(t) or STEP_LINE.match(t):
            if buf:
                paras.append(" ".join(buf))
                buf = []
            paras.append(t)
            continue
        buf.append(t)
        if t.endswith((".", "?", "!")) and len(" ".join(buf)) > 350:
            paras.append(" ".join(buf))
            buf = []
    if buf:
        paras.append(" ".join(buf))
    return paras


def _photo_for(title: str, chapter_title: str) -> Photo:
    blob = f"{title} {chapter_title}"
    room = ROOM_WORDS.search(title) or ROOM_WORDS.search(chapter_title)
    token = room.group(1).lower() if room else "interior"
    paintish = bool(re.search(r"paint|palette|colour|color|swatch|undertone|neutral|accent", blob, re.I))
    if paintish:
        subject = (
            "painted living room wall with colour samples"
            if token in {"paint", "palette", "colour", "color", "wall", "accent", "neutral", "sample"}
            else f"{token} with freshly painted walls"
        )
        query = f"indian apartment {subject} daylight editorial interior"
        flux = (
            f"Magazine editorial photograph of a compact Indian apartment {subject}, "
            "soft daylight, calm plaster walls, visible paint finish, "
            "Architectural Digest India styling, 35mm, no people, no text, photorealistic"
        )
    else:
        subject = token if token != "interior" else "apartment interior"
        query = f"small indian {subject} minimalist daylight editorial"
        flux = (
            f"Magazine editorial photograph of a compact Indian 1BHK {subject}, "
            "real Mumbai or Bangalore apartment scale, soft morning daylight from one window, "
            "calm warm-neutral walls, teak and linen textures, one deep teal accent object, "
            "clear surfaces styled like Architectural Digest India, 35mm lens, shallow depth, "
            "no people, no text, no watermark, photorealistic"
        )
    return Photo(query=query, flux_prompt=flux, alt=f"A calm {subject}")


def _figures_for(report: AuditReport, start: int, end: int) -> list[Figure]:
    figs: list[Figure] = []
    for d in report.diagrams:
        if start <= d.page <= end:
            minimum = None
            m = re.search(r"(?:do not drop below|at least|minimum(?: of)?)\s+(\d+\s*\w+)", d.source_sentence, re.I)
            if m:
                minimum = m.group(1)
            # A sentence that only states a floor ("do not drop below 28 inches")
            # is the red line on the previous diagram, not a diagram of its own.
            if minimum and len(d.values) == 1 and figs and not figs[-1].minimum:
                figs[-1].minimum = minimum
                figs[-1].caption = f"{figs[-1].caption} {d.source_sentence}"
                continue
            figs.append(
                Figure(
                    kind=d.kind,  # type: ignore[arg-type]
                    title=d.title,
                    caption=d.source_sentence,
                    values=d.values,
                    minimum=minimum,
                    spec_id=d.spec_id,
                )
            )
    return figs


def _principle(body: list[str]) -> str:
    for para in body[:3]:
        for sent in re.split(r"(?<=[.!?])\s+", para):
            sent = sent.strip()
            if 40 <= len(sent) <= 170 and sent[0].isupper() and re.search(
                r"\b(rule|at least|never|always|keep|every|must|should)\b", sent, re.I
            ):
                return sent
    return ""


def build_plan(report: AuditReport, title: str, author: str = "", series: str = "") -> BookPlan:
    doc = report.document
    chapters: list[Chapter] = []
    day = 0

    for h, start, end in _chapter_bounds(report):
        if SKIP_CHAPTER.match(h.text.strip()):
            continue
        blocks = _blocks_in(report, start, end, h.text)
        paras = _paragraphs(blocks)
        figures = _figures_for(report, start, end)

        body: list[str] = []
        steps: list[Step] = []
        checklist: list[str] = []
        mistake = ""
        mode = "body"

        for p in paras:
            if SECTION_MARKERS.match(p):
                key = p.lower()
                mode = (
                    "steps" if "step" in key or "action" in key
                    else "checklist" if "takeaway" in key
                    else "mistake" if "mistake" in key
                    else "body"
                )
                continue
            m = STEP_LINE.match(p)
            item = m.group(1).strip() if m else None
            if mode == "steps" and item:
                steps.append(Step(text=item))
            elif mode == "checklist" and item:
                checklist.append(item)
            elif mode == "mistake":
                mistake = f"{mistake} {p}".strip() if not item else f"{mistake} {item}".strip()
            elif not item and len(p) > 40:
                body.append(p)

        day += 1
        # Keep enough prose that fallback chapters stay teachable (not stubs).
        kept_body = body[:40]
        if sum(len(p) for p in kept_body) < 1500 and len(body) > 40:
            kept_body = body[:60]
        section = Section(
            slug=_slug(h.text),
            title=h.text,
            day_label=f"Part {day}",
            hero=_photo_for(h.text, h.text),
            principle=_principle(kept_body),
            body=kept_body,
            figures=figures[:8],
            steps=steps[:12],
            mistake=mistake[:800],
            checklist=checklist[:12],
        )
        chapters.append(
            Chapter(
                slug=section.slug,
                title=h.text,
                intro=(kept_body[0][:400] if kept_body else ""),
                opener=_photo_for(f"chapter opener {h.text}", h.text),
                sections=[section],
            )
        )

    metric = any("metric" in e for f in report.findings if f.rule_id == "CON001" for e in f.evidence)
    return BookPlan(
        slug=doc.slug,
        title=title,
        author=author,
        series=series,
        units="metric" if metric and doc.full_text.lower().count("centimet") > doc.full_text.lower().count("inch") else "imperial",
        chapters=chapters,
    )
