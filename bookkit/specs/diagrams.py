"""Extract diagram specifications from prose.

The premise: a decor how-to already contains its own figure list, written as
sentences. Every measurement, ratio and named framework in the text is a visual
the author described instead of drawing. This module finds them and emits a
page-anchored spec list.
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..models import Block, DiagramSpec, Document, Heading

# --- What counts as a measurable claim -----------------------------------
NUMERIC = re.compile(
    r"""(
        \d+\s*(?:to|-)\s*\d+\s*(?:inch|inches|foot|feet|centimet\w*|cm|mm|percent|lumens?|degrees?)
      | \d+\s*(?:by|x|\u00d7)\s*\d+\s*(?:inch|inches|foot|feet|centimet\w*|cm)?
      | \d+\s*(?:inch|inches|foot|feet|centimet\w*|cm|percent|lumens?)
      | \d+\s*\d+\s*\d+\s*ratio
    )""",
    re.I | re.X,
)

# Ratios written as words, e.g. "60 30 10" after ligature normalisation.
RATIO = re.compile(r"\b(\d{2})\s+(\d{2})\s+(\d{1,2})\b")

# Sentences that are pure scheduling or self-reference produce no useful figure.
NOISE = re.compile(
    r"\b(?:next chapter|you will learn|in this chapter|day \d+|week (?:one|two|three|four))\b",
    re.I,
)

# --- Named frameworks: an author-coined term is a brandable visual --------
FRAMEWORK_PATTERN = re.compile(
    r"\b((?:[A-Z][a-z]+\s+){1,4}"
    r"(?:Rule|System|Formula|Matrix|Grid|Triangle|Test|Sprint|Ratio|Zone|Kit|Ladder|Routine))\b"
)

# Map vocabulary in the sentence to the right kind of drawing.
KIND_HINTS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(?:paths?|walkways?|circulation|clearance|zones?|layout|grid|flow)\b", re.I),
     "plan", "Overhead floor plan. Shade zones, dimension every path, mark the minimum in red."),
    (re.compile(r"\brugs?\b", re.I),
     "comparison", "Two plans side by side at the same scale. Shade the wall border, highlight legs on rug."),
    (re.compile(r"\b(?:hang|mount|above|height|eye level|headboard|mirrors?|pendants?|curtains?|rods?)\b", re.I),
     "elevation", "Wall elevation with a floor baseline and vertical dimension arrows."),
    (re.compile(r"\b(?:percent of|ratio|60\s*30\s*10)\b", re.I),
     "ratio_bar", "Horizontal proportion bar, segments labelled with the actual colours."),
    (re.compile(r"\b(?:budget|allocate|storage \d|contingency)\b", re.I),
     "chart", "Donut chart, one segment per category, percentage on each."),
    (re.compile(r"\b(?:lumens?|lighting|light)\b", re.I),
     "plan", "Plan with light pooling shown as gradient circles, lumen value on each."),
    (re.compile(r"\b(?:viewing distance|diagonal|TV|screen)\b", re.I),
     "elevation", "Side elevation, viewer to screen, distance arrow with the multiplier."),
    (re.compile(r"\b(?:shelf|shelves|cabinets?|wardrobes?|counters?|fridge)\b", re.I),
     "elevation", "Front elevation of the unit with gaps dimensioned."),
]
DEFAULT_KIND = ("table", "Spec table: parameter, value, why it matters.")


def _chapter_index(doc: Document) -> list[tuple[str, int, int]]:
    h1s: list[Heading] = [h for h in doc.headings if h.level == 1]
    out = []
    for i, h in enumerate(h1s):
        end = h1s[i + 1].page - 1 if i + 1 < len(h1s) else doc.page_count
        out.append((h.text, h.page, end))
    return out


def _chapter_for(page: int, index: list[tuple[str, int, int]]) -> str:
    for title, start, end in index:
        if start <= page <= end:
            return title
    return "Front matter"


def _classify(sentence: str) -> tuple[str, str]:
    for pattern, kind, hint in KIND_HINTS:
        if pattern.search(sentence):
            return kind, hint
    return DEFAULT_KIND


def _sentences(blocks: list[Block]) -> list[tuple[int, str]]:
    """Rejoin wrapped PDF lines into sentences, keeping the starting page."""
    out: list[tuple[int, str]] = []
    buf, page = "", None
    for b in blocks:
        if page is None:
            page = b.page
        buf = f"{buf} {b.text}".strip()
        while True:
            m = re.search(r"(?<=[.!?])\s+", buf)
            if not m:
                break
            out.append((page, buf[: m.start()].strip()))
            buf = buf[m.end():]
            page = b.page
    if buf.strip():
        out.append((page or 1, buf.strip()))
    return out


# Recap sections restate specs already taught. The first statement is the one
# that needs the drawing; the recap gets a reference to it.
RECAP = re.compile(r"\b(?:key takeaway|action item|checklist|recap|summary|example)\b", re.I)


def _score(sentence: str, values: list[str], is_recap: bool) -> int:
    score = 10 * len(values)
    if FRAMEWORK_PATTERN.search(sentence):
        score += 25  # author-coined term: brandable, deserves a signature visual
    if re.search(r"\b(?:formula|rule|ratio|minimum|at least|never|do not)\b", sentence, re.I):
        score += 15  # prescriptive: reader will act on it with a tape measure
    if re.search(r"\d+\s*(?:to|-)\s*\d+", sentence):
        score += 10  # a range is a band to draw, not just a number
    if is_recap:
        score -= 30
    return score


def _topic(sentence: str) -> str:
    """Collapse restatements of the same rule onto one key."""
    nouns = re.findall(
        r"\b(rug|sofa|coffee table|console|curtain|art|path|walkway|nightstand|"
        r"mirror|pendant|shelf|shelves|counter|wardrobe|bed|TV|screen|lumens?|"
        r"budget|palette|ratio|surface)\b",
        sentence, re.I,
    )
    key = "-".join(sorted({n.lower() for n in nouns}))
    dims = "-".join(sorted(set(re.findall(r"\d+", sentence))))
    return f"{key}|{dims}" if key else f"_|{dims}"


def extract_diagrams(
    doc: Document, min_len: int = 25, max_len: int = 260, limit: int | None = 45
) -> list[DiagramSpec]:
    index = _chapter_index(doc)
    candidates: list[tuple[int, DiagramSpec]] = []
    seen_text: set[str] = set()
    best_by_topic: dict[str, tuple[int, DiagramSpec]] = {}

    for page, sentence in _sentences(doc.blocks):
        if not (min_len < len(sentence) < max_len) or NOISE.search(sentence):
            continue
        values = [v.strip() for v in NUMERIC.findall(sentence)]
        ratio = RATIO.search(sentence)
        if ratio and not values:
            values = ["-".join(ratio.groups()) + " ratio"]
        if not values:
            continue
        # "80 percent" / "20 percent" alone is the book's recurring rule, not a
        # drawable measurement. It gets a badge in layout, not a diagram slot.
        if all(re.fullmatch(r"(?:80|20)\s*percent", v, re.I) for v in values):
            continue

        fingerprint = re.sub(r"\W+", "", sentence.lower())[:80]
        if fingerprint in seen_text:
            continue
        seen_text.add(fingerprint)

        is_recap = bool(RECAP.search(sentence))
        score = _score(sentence, values, is_recap)
        kind, hint = _classify(sentence)
        spec = DiagramSpec(
            spec_id="",
            page=page,
            chapter=_chapter_for(page, index),
            kind=kind,
            title=_title_for(sentence),
            source_sentence=sentence,
            values=values,
            render_hint=hint,
            priority=score,
        )

        topic = _topic(sentence)
        prev = best_by_topic.get(topic)
        if prev is None or score > prev[0]:
            best_by_topic[topic] = (score, spec)

    candidates = sorted(best_by_topic.values(), key=lambda kv: -kv[0])
    if limit:
        candidates = candidates[:limit]

    specs = [s for _, s in sorted(candidates, key=lambda kv: (kv[1].page, -kv[0]))]
    for i, s in enumerate(specs, start=1):
        s.spec_id = f"D{i:03d}"
    return specs


def _title_for(sentence: str) -> str:
    fw = FRAMEWORK_PATTERN.search(sentence)
    if fw:
        return fw.group(1)
    words = re.sub(r"^(?:You will|Use|Keep|Choose|Ensure|Aim for|Leave|Set|Place|Mount|Hang)\s+",
                   "", sentence, flags=re.I).split()
    return " ".join(words[:7]).rstrip(",.").title()


def extract_frameworks(doc: Document) -> dict[str, list[int]]:
    """Named frameworks and the pages they appear on.

    A term the author coined and repeated is an asset. Each one should get a
    signature diagram plus a repeating badge at every mention.
    """
    hits: dict[str, set[int]] = defaultdict(set)
    for b in doc.blocks:
        for match in FRAMEWORK_PATTERN.findall(b.text):
            name = re.sub(r"\s+", " ", match).strip()
            if len(name.split()) >= 2:
                hits[name].add(b.page)
    return {k: sorted(v) for k, v in sorted(hits.items(), key=lambda kv: -len(kv[1]))}
