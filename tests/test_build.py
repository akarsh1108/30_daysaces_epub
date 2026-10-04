"""Build-stage tests: offline, mock media, synthetic PDF."""

from __future__ import annotations

from pathlib import Path

from bookkit import audit_book
from bookkit.build.config import Settings
from bookkit.build.media import svg
from bookkit.build.plan import Figure
from bookkit.build.planner import heuristic
from bookkit.build.planner.openai_planner import verify_numbers
from bookkit.build.pipeline import build

from tests.test_pipeline import make_pdf


def _cfg(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="", openai_model="x", pexels_api_key="", fal_key="",
        media_cooldown=0, media_prefer="ai", cache_dir=tmp_path / "cache",
        target_dpi=150, max_book_mb=5, trim_in=(6, 9),
        series_tagline="Test tagline",
        closing_headline="Your next room starts here",
        closing_blurb="More titles in this series.",
    )



def test_verify_numbers_catches_dropped_measurement():
    src = "Paths are 30 to 36 inches wide. Rugs need 12 to 18 inches of border."
    out = "Paths are 30 to 36 inches wide."
    missing = verify_numbers(src, out)
    assert missing and "12 to 18 inches" in missing[0]


def test_verify_numbers_passes_when_preserved():
    src = "Paths are 30 to 36 inches wide."
    assert verify_numbers(src, "Keep paths 30 to 36 inches wide always.") == []


def test_svg_plan_marks_minimum_in_red(tmp_path):
    fig = Figure(kind="plan", title="Walkways", values=["30 to 36 inches"], minimum="28 inches", spec_id="D001")
    path = svg.render(fig, tmp_path)
    s = path.read_text()
    assert "#C8442B" in s and "never below 28 in" in s and "30–36 in" in s


def test_svg_ratio_bar_three_way():
    fig = Figure(kind="ratio_bar", title="60-30-10", values=["60-30-10 ratio"])
    s = svg.ratio_bar(fig)
    assert "60%" in s and "30%" in s and "10%" in s


def test_svg_is_deterministic(tmp_path):
    fig = Figure(kind="elevation", title="Art height", values=["8 to 10 inches", "57 to 60 inches"], spec_id="D009")
    a = svg.render(fig, tmp_path / "a").read_text()
    b = svg.render(fig, tmp_path / "b").read_text()
    assert a == b


def test_svg_to_png_rasterizes_diagram_not_placeholder(tmp_path):
    from PIL import Image
    import io

    from bookkit.build.render.render import _svg_to_png_bytes

    fig = Figure(kind="table", title="The Space Is 34 Inches Wide With", values=["34 inches"], spec_id="D004")
    path = svg.render(fig, tmp_path)
    raw = _svg_to_png_bytes(path)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(raw))
    assert img.width >= 800
    pixels = list(img.getdata())
    teal = sum(1 for r, g, b in pixels if r < 50 and 70 < g < 130 and 90 < b < 140)
    assert teal > 100, "diagram PNG should include the teal table header, not an empty frame"


def test_heuristic_plan_attaches_figures_by_chapter(tmp_path):
    pdf = make_pdf(tmp_path / "b.pdf", [
        ("Introduction", ["Welcome to the book."]),
        ("Living Room Kit", [
            "Use painter tape to draw paths that are 30 to 36 inches wide.",
            "If your footprint is tight, do not drop below 28 inches.",
            "Key Takeaways",
            "- Map zones first.",
            "Action Items",
            "1. Tape your paths.",
        ]),
    ])
    report, _ = audit_book(pdf)
    plan = heuristic.build_plan(report, "Test")
    assert [c.title for c in plan.chapters] == ["Living Room Kit"]  # intro skipped
    sec = plan.chapters[0].sections[0]
    assert sec.figures and sec.figures[0].kind == "plan"
    assert sec.figures[0].minimum == "28 inches"
    assert sec.steps and "Tape" in sec.steps[0].text
    assert sec.checklist and "Map zones" in sec.checklist[0]


def test_photo_reuse_capped_at_three(tmp_path):
    from collections import Counter

    from bookkit.build.pipeline import _resolve_photos
    from bookkit.build.plan import BookPlan, Chapter, Photo, Section

    heroes = [Photo(query="same balcony plants evening", flux_prompt="same") for _ in range(7)]
    plan = BookPlan(
        slug="reuse",
        title="Reuse",
        chapters=[
            Chapter(
                slug="c",
                title="Chapter",
                opener=Photo(query="same balcony plants evening", flux_prompt="same"),
                sections=[
                    Section(slug=f"s{i}", title=f"Section {i}", hero=heroes[i])
                    for i in range(7)
                ],
            )
        ],
    )
    _resolve_photos(plan, _cfg(tmp_path), "mock")
    counts: Counter[str] = Counter()
    for p in plan.photo_slots:
        assert p.path and Path(p.path).is_file()
        counts[Path(p.path).resolve().as_posix()] += 1
    assert counts
    assert max(counts.values()) <= 3
    assert len(plan.photo_slots) == 8
    assert len(counts) >= 3


def test_end_to_end_mock_build(tmp_path):
    pdf = make_pdf(tmp_path / "b.pdf", [
        ("Introduction", ["Welcome."]),
        ("Living Room Kit", [
            "Use painter tape to draw paths that are 30 to 36 inches wide and keep them clear.",
            "Leave 12 to 18 inches of floor between rug edges and walls on the long sides.",
            "Action Items",
            "1. Tape your paths.",
        ]),
    ])
    outputs = build(pdf, "Test Book", tmp_path / "out", planner="heuristic", media="mock", cfg=_cfg(tmp_path))
    assert Path(outputs["pdf"]).exists() and Path(outputs["epub"]).exists()
    assert (tmp_path / "out" / "manifest.json").exists()
    assert (tmp_path / "out" / "plan.json").exists()
    figs = list((tmp_path / "out" / "figures").glob("*.svg"))
    assert figs
