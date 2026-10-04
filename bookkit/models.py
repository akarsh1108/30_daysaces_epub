"""Data models for the book audit pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal

Severity = Literal["blocker", "major", "minor"]


@dataclass
class Span:
    """A run of text with uniform styling."""

    text: str
    size: float
    font: str
    bold: bool
    page: int  # 1-indexed


@dataclass
class Block:
    """A paragraph-level chunk of text on a page."""

    text: str
    page: int
    size: float
    font: str
    bold: bool
    is_list_item: bool


@dataclass
class Heading:
    text: str
    page: int
    size: float
    level: int  # assigned during extraction


@dataclass
class Document:
    """Normalised representation of a source book."""

    path: str
    slug: str
    page_count: int
    page_width_pt: float
    page_height_pt: float
    word_count: int
    fonts: list[str]
    image_count: int
    images_by_page: dict[int, int]
    has_outline: bool
    outline_entries: int
    headings: list[Heading]
    blocks: list[Block]
    size_histogram: dict[float, int]
    full_text: str

    @property
    def trim_inches(self) -> tuple[float, float]:
        return (round(self.page_width_pt / 72, 2), round(self.page_height_pt / 72, 2))


@dataclass
class Finding:
    """A single audit result."""

    rule_id: str
    severity: Severity
    title: str
    detail: str
    pages: list[int] = field(default_factory=list)
    fix: str = ""
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiagramSpec:
    """A proposed visual, anchored to a page."""

    spec_id: str
    page: int
    chapter: str
    kind: str  # plan | elevation | ratio_bar | comparison | chart | table | badge
    title: str
    source_sentence: str
    values: list[str]
    render_hint: str
    priority: int = 0  # higher = build first

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditReport:
    document: Document
    findings: list[Finding]
    diagrams: list[DiagramSpec]

    def to_dict(self) -> dict[str, Any]:
        doc = asdict(self.document)
        doc.pop("blocks", None)
        doc.pop("full_text", None)
        doc["trim_inches"] = list(self.document.trim_inches)
        return {
            "document": doc,
            "findings": [f.to_dict() for f in self.findings],
            "diagrams": [d.to_dict() for d in self.diagrams],
        }
