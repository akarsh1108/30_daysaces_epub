"""Consistency checks across a book: measurement units and contradictory specs."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..models import Document, Finding, Heading

IMPERIAL = re.compile(r"\b(?:inch|inches|foot|feet|ft)\b", re.I)
METRIC = re.compile(r"\b(?:centimet(?:er|re)s?|cm|millimet(?:er|re)s?|mm|met(?:er|re)s?)\b", re.I)


def _chapters(doc: Document) -> list[tuple[str, int, int]]:
    """Return (title, start_page, end_page) for each top-level heading."""
    h1s: list[Heading] = [h for h in doc.headings if h.level == 1]
    out: list[tuple[str, int, int]] = []
    for i, h in enumerate(h1s):
        end = h1s[i + 1].page - 1 if i + 1 < len(h1s) else doc.page_count
        out.append((h.text, h.page, end))
    return out


def rule_unit_consistency(doc: Document) -> list[Finding]:
    """Catch a book that switches measurement systems between chapters."""
    text_by_page: dict[int, list[str]] = defaultdict(list)
    for b in doc.blocks:
        text_by_page[b.page].append(b.text)

    rows = []
    for title, start, end in _chapters(doc):
        body = " ".join(t for p in range(start, end + 1) for t in text_by_page[p])
        imp, met = len(IMPERIAL.findall(body)), len(METRIC.findall(body))
        if imp + met >= 5:
            rows.append((title, start, end, imp, met))

    if len(rows) < 2:
        return []

    systems = {"imperial" if imp > met else "metric" for _, _, _, imp, met in rows}
    if len(systems) < 2:
        return []

    evidence = [
        f"p{start}-{end} '{title[:52]}' -> {imp} imperial / {met} metric"
        for title, start, end, imp, met in rows
    ]
    flip_pages = [start for _, start, _, imp, met in rows if met > imp]
    return [
        Finding(
            rule_id="CON001",
            severity="major",
            title="Measurement units switch between chapters",
            detail=(
                "The book mixes imperial and metric across chapters. A reader holding one "
                "tape measure has to convert mid-project."
            ),
            pages=flip_pages,
            fix="Pick a primary unit for the whole series and put the other in parentheses.",
            evidence=evidence,
        )
    ]


# Rules stated as "<low> to <high> percent of the <noun>" that should be stable
# book-wide. Captures the range and the thing being measured.
RANGE_RULE = re.compile(
    r"(\d+)\s*(?:to|-)\s*(\d+)\s*percent\s+of\s+(?:the\s+)?([a-z ]{3,30}?)(?=[,.\s]|$)",
    re.I,
)


def rule_contradictions(doc: Document) -> list[Finding]:
    """Find the same named rule stated with different numbers in different places."""
    page_of: list[tuple[int, str]] = [(b.page, b.text) for b in doc.blocks]
    seen: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for page, text in page_of:
        for low, high, subject in RANGE_RULE.findall(text):
            key = re.sub(r"\s+", " ", subject.strip().lower())
            seen[key].append((f"{low}-{high}%", page))

    findings: list[Finding] = []
    for subject, hits in seen.items():
        ranges = Counter(r for r, _ in hits)
        if len(ranges) < 2:
            continue
        evidence = [f"p{page}: {rng} of the {subject}" for rng, page in hits]
        findings.append(
            Finding(
                rule_id="CON002",
                severity="major",
                title=f"Contradictory rule for '{subject}'",
                detail=(
                    f"The same guideline is stated as {' and '.join(ranges)} in different "
                    "places. A reader following one will disagree with the other."
                ),
                pages=sorted({p for _, p in hits}),
                fix="Pick one range, correct every instance, and lock it in a spec table.",
                evidence=evidence,
            )
        )
    return findings


ALL_RULES = [rule_unit_consistency, rule_contradictions]


def run_consistency(doc: Document) -> list[Finding]:
    findings: list[Finding] = []
    for rule in ALL_RULES:
        findings.extend(rule(doc))
    return findings
