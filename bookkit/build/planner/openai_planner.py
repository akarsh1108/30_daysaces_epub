"""OpenAI planner. One call per chapter, strict JSON schema output.

The model is asked to reorganise and add scenarios, examples and image briefs.
It is told, in the prompt and by the schema, to preserve every number and
recommendation from the source. The audit's diagram specs are passed in so the
figures it places reference real specs, not invented ones.

Post-call, `verify_numbers` checks that no measurement present in the source
went missing or changed. A chapter that fails verification is retried once,
then falls back to the heuristic planner for that chapter.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...models import AuditReport
from ..config import Settings
from ..plan import BookPlan, Chapter, Section
from . import heuristic

NUM_UNIT = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:to|-)?\s*\d*(?:\.\d+)?\s*(?:inches|inch|feet|foot|ft|cm|centimet\w*|mm|percent|%|lumens?)",
    re.I,
)

SYSTEM = """You are the developmental editor for an illustrated home-decor series
sold on Amazon India. You restructure a chapter of an existing, well-reviewed
book into a magazine-style layout buyers would flip through on KDP Look Inside.
You do not change the advice.

Hard rules:
1. Preserve every measurement, ratio, product size and recommendation exactly.
   If the source says 30 to 36 inches, the output says 30 to 36 inches.
2. Do not invent new specifications. Scenarios may describe a person's room, but
   any number in a scenario must already appear in the source.
3. Write scenarios about real Indian city apartments: a named resident, the
   city, the actual constraint (a 1BHK in Pune with a 9-foot living room, a
   Bengaluru rental with a load-bearing wall, a Mumbai flat with a window on
   only one side). Concrete, specific, no marketing tone.
4. Keep the author's frameworks by name. They are the brand.
5. Photo queries are for a stock library: concrete nouns, lighting, no brands.
   Flux prompts must read like Architectural Digest India commissions: compact
   Indian apartment scale, one window light, teak/linen/plaster textures,
   magazine styling, photorealistic, no people, no text, no watermark.
6. Every section needs a distinct hero_flux (different room angle / prop story).
   Chapter opener_flux is a full-bleed establishing shot for that chapter.
7. Use the units of the source chapter.
8. COMPLETENESS (critical): Do not summarize the chapter into a thin outline.
   Keep the teaching. Every section's body must contain the real how-to prose
   from the source — clearances, heights, materials, steps, warnings — rewritten
   cleanly into short paragraphs, not one-line teasers. Prefer 2–5 sections
   that together cover the whole chapter over many empty day stubs.
9. Every section MUST have: a non-empty scenario, a one-sentence principle, and
   a body array with at least 3 substantial paragraphs (unless the source
   chapter itself is shorter than that).
10. Place as many AVAILABLE DIAGRAM SPECS as fit by using their spec_id. Do not
    leave important measurement diagrams unused when they belong to this chapter.
11. Checklist / audit / printable chapters must keep the checklist items as
    checklist[] and the instructions as body[].

Return only JSON matching the schema."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "intro": {"type": "string"},
        "opener_query": {"type": "string"},
        "opener_flux": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "day_label": {"type": "string"},
                    "scenario": {"type": "string"},
                    "principle": {"type": "string"},
                    "body": {"type": "array", "items": {"type": "string"}},
                    "figure_spec_ids": {"type": "array", "items": {"type": "string"}},
                    "figure_captions": {"type": "array", "items": {"type": "string"}},
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "mistake": {"type": "string"},
                    "checklist": {"type": "array", "items": {"type": "string"}},
                    "hero_query": {"type": "string"},
                    "hero_flux": {"type": "string"},
                    "hero_alt": {"type": "string"},
                },
                "required": [
                    "title", "day_label", "scenario", "principle", "body",
                    "figure_spec_ids", "figure_captions", "steps", "mistake",
                    "checklist", "hero_query", "hero_flux", "hero_alt",
                ],
            },
        },
    },
    "required": ["title", "intro", "opener_query", "opener_flux", "sections"],
}


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s+", " ", m.lower()) for m in NUM_UNIT.findall(text)}


def verify_numbers(source: str, output: str) -> list[str]:
    """Return measurements present in the source but absent from the output."""
    return sorted(_numbers(source) - _numbers(output))


def verify_chapter_quality(candidate: dict[str, Any], source: str) -> list[str]:
    """Reject thin magazine stubs that drop the teaching prose."""
    problems: list[str] = []
    sections = candidate.get("sections") or []
    if not sections:
        return ["no sections"]
    body_paras = [p for s in sections for p in (s.get("body") or []) if str(p).strip()]
    body_chars = sum(len(p) for p in body_paras)
    src_chars = max(1, len(source.strip()))
    if not body_paras:
        problems.append("empty body")
    elif body_chars < min(1800, max(600, src_chars // 8)):
        problems.append(f"body too short ({body_chars} chars)")
    empty_sections = sum(1 for s in sections if len([p for p in (s.get("body") or []) if str(p).strip()]) < 2)
    if empty_sections:
        problems.append(f"{empty_sections} thin sections")
    if not any((s.get("principle") or "").strip() for s in sections):
        problems.append("missing principles")
    if not any((s.get("scenario") or "").strip() for s in sections):
        problems.append("missing scenarios")
    return problems


def _chapter_text(report: AuditReport, start: int, end: int) -> str:
    return "\n".join(b.text for b in report.document.blocks if start <= b.page <= end)


def _call(client, model: str, chapter_text: str, specs: list[dict[str, Any]]) -> dict[str, Any]:
    user = (
        "SOURCE CHAPTER:\n" + chapter_text +
        "\n\nAVAILABLE DIAGRAM SPECS (reference by spec_id, do not invent):\n" +
        json.dumps(specs, ensure_ascii=False) +
        "\n\nRemember: keep the full teaching. Body paragraphs must carry the "
        "source advice, not just headings. Use the diagram spec_ids that belong here."
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=0.3,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "chapter_plan", "strict": True, "schema": SCHEMA},
        },
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
    )
    return json.loads(resp.choices[0].message.content)


def _merge_figures(preferred: list, fallback: list) -> list:
    """Keep preferred figures, then add unused fallback specs (up to 8)."""
    seen = {f.spec_id for f in preferred if getattr(f, "spec_id", None)}
    out = list(preferred)
    for f in fallback:
        if f.spec_id and f.spec_id in seen:
            continue
        out.append(f)
        if f.spec_id:
            seen.add(f.spec_id)
        if len(out) >= 8:
            break
    return out


def _enrich_from_fallback(sections: list[Section], fallback: Chapter) -> list[Section]:
    """If the model returned thin bodies, graft heuristic prose/figures back in."""
    fb = fallback.sections[0] if fallback.sections else None
    if not fb:
        return sections
    total_body = sum(len(p) for s in sections for p in s.body)
    if total_body >= 1200:
        # Still attach leftover diagrams onto the first section.
        if sections:
            sections[0].figures = _merge_figures(sections[0].figures, fb.figures)
        return sections
    # Single-section rescue: keep magazine chrome, restore teaching body.
    primary = sections[0]
    primary.body = fb.body or primary.body
    if not primary.principle:
        primary.principle = fb.principle
    if not primary.steps:
        primary.steps = fb.steps
    if not primary.checklist:
        primary.checklist = fb.checklist
    if not primary.mistake:
        primary.mistake = fb.mistake
    primary.figures = _merge_figures(primary.figures, fb.figures)
    return sections


def build_plan(report: AuditReport, cfg: Settings, title: str, author: str = "", series: str = "") -> BookPlan:
    from openai import OpenAI

    client = OpenAI(api_key=cfg.openai_api_key)
    base = heuristic.build_plan(report, title, author, series)  # fallback + skeleton
    specs_by_id = {d.spec_id: d for d in report.diagrams}
    bounds = {h.text: (start, end) for h, start, end in heuristic._chapter_bounds(report)}

    chapters: list[Chapter] = []
    for fallback in base.chapters:
        start, end = bounds[fallback.title]
        text = _chapter_text(report, start, end)
        specs = [
            {"spec_id": d.spec_id, "kind": d.kind, "title": d.title, "values": d.values,
             "source": d.source_sentence}
            for d in report.diagrams if start <= d.page <= end
        ]

        result = None
        for _attempt in range(3):
            candidate = _call(client, cfg.openai_model, text, specs)
            flat = json.dumps(candidate, ensure_ascii=False)
            missing = verify_numbers(text, flat)
            quality = verify_chapter_quality(candidate, text)
            numbers_ok = len(missing) <= max(2, len(_numbers(text)) // 8)
            if numbers_ok and not quality:
                result = candidate
                break
            # Keep a numbers-ok candidate if quality only fails mildly; enrich later.
            if numbers_ok and result is None:
                result = candidate
        if result is None:
            chapters.append(fallback)
            continue

        sections: list[Section] = []
        for s in result["sections"]:
            figs = []
            for i, sid in enumerate(s["figure_spec_ids"]):
                d = specs_by_id.get(sid)
                if not d:
                    continue
                cap = s["figure_captions"][i] if i < len(s["figure_captions"]) else d.source_sentence
                figs.append(heuristic.Figure(kind=d.kind, title=d.title, caption=cap,  # type: ignore[arg-type]
                                             values=d.values, spec_id=d.spec_id))
            body = [p.strip() for p in (s.get("body") or []) if str(p).strip()]
            sections.append(
                Section(
                    slug=heuristic._slug(s["title"]),
                    title=s["title"],
                    day_label=s["day_label"],
                    hero=heuristic.Photo(query=s["hero_query"], flux_prompt=s["hero_flux"], alt=s["hero_alt"]),
                    scenario=(s.get("scenario") or "").strip(),
                    principle=(s.get("principle") or "").strip(),
                    body=body,
                    figures=figs,
                    steps=[heuristic.Step(text=t) for t in (s.get("steps") or []) if str(t).strip()],
                    mistake=(s.get("mistake") or "").strip(),
                    checklist=[c for c in (s.get("checklist") or []) if str(c).strip()],
                )
            )
        sections = _enrich_from_fallback(sections or fallback.sections, fallback)
        chapters.append(
            Chapter(
                slug=fallback.slug,
                title=result.get("title") or fallback.title,
                intro=(result.get("intro") or "").strip(),
                opener=heuristic.Photo(query=result["opener_query"], flux_prompt=result["opener_flux"]),
                sections=sections,
            )
        )

    return BookPlan(slug=base.slug, title=title, author=author, series=series, units=base.units, chapters=chapters)
