"""Deterministic audit rules. No LLM required.

Each rule takes a Document and returns zero or more Findings.
Add a rule by writing a function and appending it to ALL_RULES.
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..models import Document, Finding

# --- Publishing constants -------------------------------------------------
# KDP standard trims in inches. A4 (8.27 x 11.69) is not among them.
KDP_TRIMS = {(5.0, 8.0), (5.25, 8.0), (5.5, 8.5), (6.0, 9.0), (7.0, 10.0), (8.5, 11.0)}
KDP_DELIVERY_FEE_PER_MB_USD = 0.15
TARGET_MAX_MB = 5.0
MIN_HEADING_LEVELS = 3
FRONT_MATTER_MAX_PCT = 5.0  # intro should not eat the Look Inside preview
LOOK_INSIDE_PCT = 10.0


def rule_trim_size(doc: Document) -> list[Finding]:
    w, h = doc.trim_inches
    if (w, h) in KDP_TRIMS:
        return []
    is_a4 = abs(w - 8.27) < 0.15 and abs(h - 11.69) < 0.15
    label = "A4" if is_a4 else f"{w}x{h} in"
    return [
        Finding(
            rule_id="FMT001",
            severity="blocker",
            title=f"Page size is {label}, not a book trim",
            detail=(
                f"The file is {w} x {h} inches. KDP print trims are 5x8, 5.25x8, "
                f"5.5x8.5, 6x9, 7x10 and 8.5x11. {label} is a document size, not a "
                "book size, and inflates page count."
            ),
            fix="Rebuild the interior at 6x9 in for print. Ship Kindle as reflowable EPUB3.",
        )
    ]


def rule_heading_hierarchy(doc: Document) -> list[Finding]:
    levels = sorted({h.level for h in doc.headings})
    if len(levels) >= MIN_HEADING_LEVELS:
        return []
    sizes = sorted({h.size for h in doc.headings}, reverse=True)
    body = max(doc.size_histogram, key=doc.size_histogram.get) if doc.size_histogram else 0
    return [
        Finding(
            rule_id="FMT002",
            severity="blocker",
            title=f"Only {len(levels)} heading level(s) detected",
            detail=(
                f"Heading sizes found: {sizes or 'none'}pt against {body}pt body text. "
                "Sub-sections are set identically to body copy, so the reader cannot see "
                "where one idea ends and the next begins. This is the chapter-division problem."
            ),
            fix="Define H1/H2/H3 as real styles (e.g. 20/15/12pt) and apply them throughout.",
        )
    ]


def rule_missing_outline(doc: Document) -> list[Finding]:
    if doc.has_outline:
        return []
    return [
        Finding(
            rule_id="FMT003",
            severity="blocker",
            title="No table of contents or PDF outline",
            detail=(
                f"{len(doc.headings)} headings exist in the text but the file carries "
                "0 outline entries. There is no navigation on any device."
            ),
            fix="Generate a linked TOC from H1/H2 and embed a document outline on export.",
        )
    ]


def rule_body_font(doc: Document) -> list[Finding]:
    findings: list[Finding] = []
    fallbacks = [f for f in doc.fonts if re.search(r"nimbus|helvetica|liberation", f, re.I)]
    if fallbacks:
        findings.append(
            Finding(
                rule_id="FMT004",
                severity="major",
                title="Body set in a fallback font",
                detail=(
                    f"Fonts in use: {', '.join(doc.fonts)}. These are default substitution "
                    "faces, not chosen typography, and read as unfinished in print."
                ),
                fix="Licence a serif body face for print. Keep sans for headings only.",
            )
        )
    body = max(doc.size_histogram, key=doc.size_histogram.get) if doc.size_histogram else 0
    if body >= 14:
        findings.append(
            Finding(
                rule_id="FMT005",
                severity="major",
                title=f"Body text is {body}pt",
                detail=(
                    f"{body}pt body on a {doc.trim_inches[0]}x{doc.trim_inches[1]} page is "
                    f"why {doc.word_count:,} words occupy {doc.page_count} pages. At 6x9 with "
                    f"11pt this is roughly {estimate_pages(doc.word_count)} pages."
                ),
                fix="Set body to 10.5-11pt with 1.35 line height at 6x9.",
            )
        )
    return findings


def estimate_pages(word_count: int, words_per_page: int = 320) -> int:
    return max(1, round(word_count / words_per_page))


def rule_image_density(doc: Document) -> list[Finding]:
    per_page = doc.image_count / doc.page_count if doc.page_count else 0
    if per_page >= 0.25:
        return []
    cover_only = set(doc.images_by_page) <= {1}
    where = "cover only" if cover_only else f"pages {sorted(doc.images_by_page)[:8]}"
    return [
        Finding(
            rule_id="VIS001",
            severity="blocker",
            title=f"{doc.image_count} image(s) across {doc.page_count} pages ({where})",
            detail=(
                "A design and decor title carrying no interior visuals reads as a text "
                "product in a visual category. Reader expectation is a diagram every 3-4 pages."
            ),
            fix=f"Target {max(25, doc.page_count // 4)} interior visuals. See the diagram spec list.",
        )
    ]


def rule_front_matter_length(doc: Document) -> list[Finding]:
    h1s = [h for h in doc.headings if h.level == 1]
    if len(h1s) < 2:
        return []
    first_chapter_page = h1s[1].page
    pct = first_chapter_page / doc.page_count * 100
    if pct <= FRONT_MATTER_MAX_PCT:
        return []
    preview_pages = round(doc.page_count * LOOK_INSIDE_PCT / 100)
    return [
        Finding(
            rule_id="CNV001",
            severity="blocker",
            title=f"Front matter runs {first_chapter_page - 1} pages before chapter 1",
            detail=(
                f"'{h1s[1].text}' starts on page {first_chapter_page} "
                f"({pct:.0f}% in). Amazon's Look Inside shows roughly the first "
                f"{LOOK_INSIDE_PCT:.0f}% ({preview_pages} pages), so a browsing buyer sees "
                "only promises and never reaches a usable technique."
            ),
            pages=list(range(1, first_chapter_page)),
            fix="Cut front matter to 3-4 pages and pull one complete technique forward into it.",
        )
    ]


def rule_prose_density(doc: Document) -> list[Finding]:
    """Flag chapters that are walls of prose with no scannable structure."""
    by_page: dict[int, list] = defaultdict(list)
    for b in doc.blocks:
        by_page[b.page].append(b)

    dense: list[int] = []
    for page, blks in by_page.items():
        body = [b for b in blks if not b.bold]
        if len(body) < 15:
            continue
        if not any(b.is_list_item for b in blks):
            dense.append(page)

    if len(dense) < doc.page_count * 0.15:
        return []
    return [
        Finding(
            rule_id="FMT006",
            severity="major",
            title=f"{len(dense)} pages are unbroken prose",
            detail=(
                "These pages carry a full column of text with no list, callout, table or "
                "figure. In a how-to title this is where readers stop."
            ),
            pages=dense[:40],
            fix="Convert specs to tables, add a callout box per page, insert the mapped diagrams.",
        )
    ]


def rule_file_size(doc: Document) -> list[Finding]:
    from pathlib import Path

    mb = Path(doc.path).stat().st_size / 1_048_576
    if mb <= TARGET_MAX_MB:
        return []
    fee = mb * KDP_DELIVERY_FEE_PER_MB_USD
    return [
        Finding(
            rule_id="ROY001",
            severity="major",
            title=f"File is {mb:.1f} MB",
            detail=(
                f"At the 70% royalty tier KDP deducts ${KDP_DELIVERY_FEE_PER_MB_USD:.2f}/MB, "
                f"about ${fee:.2f} off every sale."
            ),
            fix="Compress assets to 150 DPI, or move to the 35% tier which has no delivery fee.",
        )
    ]


ALL_RULES = [
    rule_trim_size,
    rule_heading_hierarchy,
    rule_missing_outline,
    rule_body_font,
    rule_image_density,
    rule_front_matter_length,
    rule_prose_density,
    rule_file_size,
]


def run_rules(doc: Document) -> list[Finding]:
    findings: list[Finding] = []
    for rule in ALL_RULES:
        findings.extend(rule(doc))
    order = {"blocker": 0, "major": 1, "minor": 2}
    return sorted(findings, key=lambda f: (order[f.severity], f.rule_id))
