"""Render audit results as a page-anchored Markdown worklist plus JSON."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .models import AuditReport

BADGE = {"blocker": "BLOCKER", "major": "MAJOR", "minor": "MINOR"}


def to_markdown(report: AuditReport, frameworks: dict[str, list[int]]) -> str:
    doc = report.document
    w, h = doc.trim_inches
    lines: list[str] = []
    add = lines.append

    add(f"# Revision worklist: {doc.slug}")
    add("")
    add(f"- Pages: {doc.page_count} | Words: {doc.word_count:,} | Trim: {w} x {h} in")
    add(f"- Fonts: {', '.join(doc.fonts)}")
    add(f"- Interior images: {doc.image_count} | Outline entries: {doc.outline_entries}")
    add("")

    counts = defaultdict(int)
    for f in report.findings:
        counts[f.severity] += 1
    summary = ", ".join(f"{counts[s]} {s}" for s in ("blocker", "major", "minor") if counts[s])
    add(f"**{len(report.findings)} findings** ({summary}) and "
        f"**{len(report.diagrams)} diagram slots**.")
    add("")

    add("## Findings")
    add("")
    for f in report.findings:
        add(f"### [{BADGE[f.severity]}] {f.rule_id} - {f.title}")
        add("")
        add(f.detail)
        add("")
        if f.pages:
            shown = ", ".join(str(p) for p in f.pages[:25])
            more = f" (+{len(f.pages) - 25} more)" if len(f.pages) > 25 else ""
            add(f"- Pages: {shown}{more}")
        for e in f.evidence:
            add(f"- {e}")
        if f.fix:
            add(f"- **Fix:** {f.fix}")
        add("")

    if frameworks:
        add("## Named frameworks")
        add("")
        add("Author-coined terms. Each needs one signature diagram and a repeating "
            "badge at every mention.")
        add("")
        add("| Framework | Mentions | First page | Pages |")
        add("|---|---|---|---|")
        for name, pages in list(frameworks.items())[:15]:
            shown = ", ".join(str(p) for p in pages[:8])
            if len(pages) > 8:
                shown += ", ..."
            add(f"| {name} | {len(pages)} | {pages[0]} | {shown} |")
        add("")

    add("## Diagram insertion plan")
    add("")
    by_chapter: dict[str, list] = defaultdict(list)
    for d in report.diagrams:
        by_chapter[d.chapter].append(d)

    for chapter, specs in by_chapter.items():
        add(f"### {chapter} ({len(specs)} visuals)")
        add("")
        add("| ID | Page | Kind | Visual | Values | Pri |")
        add("|---|---|---|---|---|---|")
        for d in specs:
            vals = "; ".join(d.values[:4]).replace("|", "/")
            title = d.title.replace("|", "/")
            add(f"| {d.spec_id} | {d.page} | {d.kind} | {title} | {vals} | {d.priority} |")
        add("")

    add("## Render hints")
    add("")
    for d in report.diagrams:
        add(f"**{d.spec_id}** (p{d.page}, {d.kind}) - {d.title}")
        add("")
        add(f"> {d.source_sentence}")
        add("")
        add(f"{d.render_hint}")
        add("")

    return "\n".join(lines)


def write(report: AuditReport, frameworks: dict[str, list[int]], outdir: str | Path) -> dict[str, Path]:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    slug = report.document.slug

    md_path = out / f"{slug}.worklist.md"
    json_path = out / f"{slug}.audit.json"

    md_path.write_text(to_markdown(report, frameworks), encoding="utf-8")
    payload = report.to_dict()
    payload["frameworks"] = frameworks
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"markdown": md_path, "json": json_path}
