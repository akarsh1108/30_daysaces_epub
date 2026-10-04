"""Tests build synthetic PDFs so the suite runs without any real book."""

from __future__ import annotations

import pymupdf
import pytest

from bookkit import audit_book, extract
from bookkit.audit.consistency import rule_contradictions, rule_unit_consistency
from bookkit.audit.rules import rule_heading_hierarchy, rule_trim_size
from bookkit.specs.diagrams import extract_diagrams, extract_frameworks


def make_pdf(path, pages, width=595, height=842):
    """pages: list of (heading|None, [body lines])"""
    doc = pymupdf.open()
    for heading, body in pages:
        page = doc.new_page(width=width, height=height)
        y = 60
        if heading:
            page.insert_text((50, y), heading, fontsize=24, fontname="helv")
            y += 40
        for line in body:
            page.insert_text((50, y), line, fontsize=16, fontname="helv")
            y += 22
        doc.save(path, incremental=False) if False else None
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def a4_book(tmp_path):
    p = tmp_path / "sample-book.pdf"
    make_pdf(p, [
        ("Introduction", ["This book will change your home.", "You will learn a lot."]),
        ("Living Room Kit", [
            "Use painter tape to draw paths that are 30 to 36 inches wide.",
            "Leave 12 to 18 inches of floor between rug edges and walls.",
            "Ensure at least 6 inches of rug under the front legs.",
            "Keep 16 to 18 inches between the sofa and the coffee table.",
            "If your sofa is 72 inches, aim for a 48 inches table.",
            "Choose art that is 60 to 75 percent of the sofa width.",
            "The Zonal Layout Grid organises three zones.",
        ]),
        ("Bedroom Kit", [
            "Aim for at least 60 centimeters clearance on each side.",
            "The Prime Zone is from about 80 to 150 centimeters off the floor.",
            "Pendant drop sits 45 to 55 centimeters above the nightstand.",
            "Mirror bottom sits 95 to 105 centimeters from the floor.",
            "Art hangs 15 to 25 centimeters above the headboard.",
            "Nightstand top sits within 2 to 5 centimeters of the mattress.",
            "Choose art that is 50 to 75 percent of the sofa width.",
        ]),
    ])
    return p


def test_extract_basics(a4_book):
    doc = extract(a4_book)
    assert doc.page_count == 3
    assert doc.word_count > 40
    assert len(doc.headings) == 3
    assert doc.headings[0].text == "Introduction"


def test_trim_size_flags_a4(a4_book):
    doc = extract(a4_book)
    findings = rule_trim_size(doc)
    assert len(findings) == 1
    assert findings[0].rule_id == "FMT001"
    assert "A4" in findings[0].title


def test_trim_size_accepts_6x9(tmp_path):
    p = make_pdf(tmp_path / "ok.pdf", [("Ch1", ["Body text here."])],
                 width=6 * 72, height=9 * 72)
    assert rule_trim_size(extract(p)) == []


def test_single_heading_level_is_flagged(a4_book):
    findings = rule_heading_hierarchy(extract(a4_book))
    assert findings and findings[0].rule_id == "FMT002"


def test_unit_switch_detected(a4_book):
    findings = rule_unit_consistency(extract(a4_book))
    assert findings, "expected imperial->metric switch to be caught"
    assert findings[0].rule_id == "CON001"
    assert any("metric" in e for e in findings[0].evidence)


def test_contradiction_detected(a4_book):
    findings = rule_contradictions(extract(a4_book))
    subjects = [f.title for f in findings]
    assert any("sofa" in s for s in subjects)
    hit = next(f for f in findings if "sofa" in f.title)
    assert "60-75%" in hit.detail and "50-75%" in hit.detail


def test_diagrams_are_page_anchored(a4_book):
    doc = extract(a4_book)
    specs = extract_diagrams(doc)
    assert specs
    assert all(1 <= s.page <= doc.page_count for s in specs)
    assert all(s.values for s in specs)
    assert all(s.spec_id.startswith("D") for s in specs)
    # ids run in page order after ranking
    assert [s.page for s in specs] == sorted(s.page for s in specs)


def test_diagram_limit_respected(a4_book):
    assert len(extract_diagrams(extract(a4_book), limit=2)) == 2


def test_walkway_sentence_becomes_a_plan(a4_book):
    specs = extract_diagrams(extract(a4_book))
    walk = next(s for s in specs if "30 to 36" in s.source_sentence)
    assert walk.kind == "plan"


def test_frameworks_found(a4_book):
    fw = extract_frameworks(extract(a4_book))
    assert any("Zonal Layout Grid" in k for k in fw)


def test_end_to_end(a4_book):
    report, frameworks = audit_book(a4_book)
    assert report.findings
    assert report.diagrams
    payload = report.to_dict()
    assert "document" in payload and "findings" in payload
    assert "blocks" not in payload["document"]  # kept out of JSON on purpose
