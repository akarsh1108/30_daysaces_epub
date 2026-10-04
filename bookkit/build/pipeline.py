"""Orchestrates: audit -> plan -> media -> render -> guard -> manifest."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .. import audit_book
from .config import Settings, settings
from .media import photos, svg
from .media.photos import MAX_PHOTO_USES, identity as photo_identity
from .plan import BookPlan, Photo
from .planner import heuristic
from .render import render


def apply_series_branding(plan: BookPlan, cfg: Settings) -> None:
    """Shared series fields from .env (tagline / closing copy only — no store URL)."""
    if not plan.tagline:
        plan.tagline = cfg.series_tagline
    if not plan.closing_headline:
        plan.closing_headline = cfg.closing_headline
    if not plan.closing_blurb:
        plan.closing_blurb = cfg.closing_blurb


def seed_cover(plan: BookPlan) -> None:
    """Put the cover in the photo queue *before* resolve so it counts toward the reuse cap."""
    if plan.cover:
        return
    for ch in plan.chapters:
        src = ch.opener
        if not src:
            for s in ch.sections:
                if s.hero:
                    src = s.hero
                    break
        if src:
            plan.cover = src.model_copy(update={"path": None, "alt": src.alt or plan.title})
            return


def ensure_cover(plan: BookPlan) -> None:
    """Cover uses the first resolved chapter opener / hero photo."""
    if plan.cover and plan.cover.path:
        return
    for ch in plan.chapters:
        if ch.opener and ch.opener.path:
            plan.cover = Photo(
                query=ch.opener.query,
                flux_prompt=ch.opener.flux_prompt,
                alt=ch.opener.alt or plan.title,
                source=ch.opener.source,
                path=ch.opener.path,
                credit=ch.opener.credit,
                ai_generated=ch.opener.ai_generated,
                provider_id=ch.opener.provider_id,
            )
            return
        for s in ch.sections:
            if s.hero and s.hero.path:
                plan.cover = Photo(
                    query=s.hero.query,
                    flux_prompt=s.hero.flux_prompt,
                    alt=s.hero.alt or plan.title,
                    source=s.hero.source,
                    path=s.hero.path,
                    credit=s.hero.credit,
                    ai_generated=s.hero.ai_generated,
                    provider_id=s.hero.provider_id,
                )
                return


def _copy_photo(src: Photo, dest: Photo) -> None:
    dest.path = src.path
    dest.source = src.source
    dest.credit = src.credit
    dest.ai_generated = src.ai_generated
    dest.provider_id = src.provider_id


def _resolve_photos(plan: BookPlan, cfg: Settings, mode: str) -> None:
    """Fill every photo slot. The same image may appear at most MAX_PHOTO_USES times."""
    uses: dict[str, int] = {}
    variants: dict[str, list[Photo]] = {}

    for p in plan.photo_slots:
        key = f"{p.query}|{p.flux_prompt}"
        banned = {ident for ident, n in uses.items() if n >= MAX_PHOTO_USES}
        assigned = False
        for prev in variants.get(key, []):
            ident = photo_identity(prev)
            if uses.get(ident, 0) < MAX_PHOTO_USES:
                _copy_photo(prev, p)
                assigned = True
                break
        if not assigned:
            photos.resolve(p, cfg, mode=mode, banned=banned)
            ident = photo_identity(p)
            if uses.get(ident, 0) >= MAX_PHOTO_USES:
                photos.mock(p, cfg, banned=banned | {ident})
            variants.setdefault(key, []).append(p.model_copy())
        ident = photo_identity(p)
        uses[ident] = uses.get(ident, 0) + 1


def _enforce_reuse_cap(plan: BookPlan, cfg: Settings, mode: str) -> None:
    """Re-resolve any slot that would push an image past MAX_PHOTO_USES (e.g. cover copy)."""
    uses: dict[str, int] = {}
    for p in plan.photo_slots:
        if p.path and Path(p.path).name.lower() == "cover.jpg":
            ident = photo_identity(p)
            uses[ident] = uses.get(ident, 0) + 1
            continue
        ident = photo_identity(p)
        if uses.get(ident, 0) >= MAX_PHOTO_USES:
            banned = {i for i, n in uses.items() if n >= MAX_PHOTO_USES} | {ident}
            photos.resolve(p, cfg, mode=mode, banned=banned)
            new_id = photo_identity(p)
            if uses.get(new_id, 0) >= MAX_PHOTO_USES:
                photos.mock(p, cfg, banned=banned | {new_id})
            ident = photo_identity(p)
        uses[ident] = uses.get(ident, 0) + 1


def _stage_photos(plan: BookPlan, outdir: Path) -> None:
    """Copy every photo into outdir/photos and rewrite ALL slots to those paths.

    Critical: many heroes share one cache file. Deduping by path would leave
    most slots pointing at .cache/, which breaks EPUB + browser preview.
    """
    photo_dir = outdir / "photos"
    photo_dir.mkdir(parents=True, exist_ok=True)
    path_map: dict[str, str] = {}
    for i, p in enumerate(plan.photo_slots):
        if not p.path:
            continue
        src = Path(p.path)
        if not src.is_file():
            src = Path.cwd() / src
        if not src.is_file():
            print(f"  missing photo: {p.path}")
            p.path = None
            continue
        # Keep an already-designed cover.jpg in place so rerender never
        # copies it into photos/ or restages it as a stock opener.
        if src.name.lower() == "cover.jpg":
            resolved = str(src.resolve())
            path_map[resolved] = resolved
            p.path = resolved
            continue
        key = str(src.resolve())
        if key in path_map:
            p.path = path_map[key]
            continue
        dest = photo_dir / f"{i:03d}-{p.source or 'img'}{src.suffix.lower() or '.jpg'}"
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        path_map[key] = str(dest.resolve())
        p.path = path_map[key]


def _stage_figures(plan: BookPlan, outdir: Path) -> None:
    """Keep figure paths inside the book folder (absolute -> relative-safe)."""
    fig_dir = (outdir / "figures").resolve()
    for f in plan.all_figures:
        if not f.path:
            continue
        src = Path(f.path)
        if not src.is_file():
            src = Path.cwd() / src
        if not src.is_file():
            continue
        dest = fig_dir / src.name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        f.path = str(dest)


def build(
    pdf: Path,
    title: str,
    outdir: Path,
    author: str = "",
    series: str = "",
    planner: str = "auto",
    media: str = "live",
    cfg: Settings | None = None,
    max_diagrams: int = 45,
) -> dict[str, str]:
    cfg = cfg or settings()
    outdir.mkdir(parents=True, exist_ok=True)
    log = print

    log(f"[1/5] audit      {pdf.name}")
    report, _frameworks = audit_book(pdf, max_diagrams=max_diagrams)

    use_openai = planner == "openai" or (planner == "auto" and cfg.has_openai)
    log(f"[2/5] plan       {'openai' if use_openai else 'heuristic'}")
    if use_openai:
        from .planner import openai_planner
        plan = openai_planner.build_plan(report, cfg, title, author, series)
    else:
        plan = heuristic.build_plan(report, title, author, series)
    apply_series_branding(plan, cfg)
    (outdir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    prefer = f"{cfg.media_prefer}-first" if media == "live" else media
    log(f"[3/5] media      {len(plan.all_figures)} diagrams, {len(plan.photo_slots)} photo slots ({prefer})")
    fig_dir = outdir / "figures"
    for f in plan.all_figures:
        f.path = str(svg.render(f, fig_dir))
    seed_cover(plan)
    _resolve_photos(plan, cfg, mode=media)
    ensure_cover(plan)
    _enforce_reuse_cap(plan, cfg, mode=media)
    _stage_photos(plan, outdir)
    _stage_figures(plan, outdir)
    (outdir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    log("[4/5] render     html, pdf, epub, cover")
    html = render.to_html(plan, outdir)
    (outdir / "book.html").write_text(html, encoding="utf-8")
    pdf_out = render.to_pdf(html, outdir / f"{plan.slug}.pdf", outdir)
    cover_out = render.to_cover_pdf(plan, outdir / "cover.pdf", outdir)
    epub_out = render.to_epub(plan, outdir / f"{plan.slug}.epub")
    preview = render.write_epub_preview(plan, outdir)

    log("[5/5] guard")
    outputs = {"cover": str(cover_out)}
    for label, path in (("pdf", pdf_out), ("epub", epub_out)):
        mb, ok = render.size_guard(path, cfg)
        outputs[label] = str(path)
        flag = "" if ok else f"  <-- over {cfg.max_book_mb} MB: delivery fee risk on 70% tier"
        log(f"      {label}: {mb} MB{flag}")
    outputs["epub_preview"] = str(preview)
    log(f"      cover: {cover_out}")
    log(f"      preview: {preview}")
    if plan.ai_image_count:
        log(f"      {plan.ai_image_count} AI images: declare on KDP")
    else:
        log("      0 AI images (stock/mock only)")

    render.write_manifest(plan, outdir, outputs)
    return outputs


def rebuild_from_plan(plan_path: Path, outdir: Path, cfg: Settings | None = None) -> dict[str, str]:
    """Re-render after hand-editing plan.json. Skips audit and planning."""
    cfg = cfg or settings()
    plan = BookPlan.model_validate(json.loads(plan_path.read_text(encoding="utf-8")))
    apply_series_branding(plan, cfg)
    ensure_cover(plan)
    _enforce_reuse_cap(plan, cfg, mode="live")
    _stage_photos(plan, outdir)
    _stage_figures(plan, outdir)
    (outdir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    html = render.to_html(plan, outdir)
    (outdir / "book.html").write_text(html, encoding="utf-8")
    pdf_out = render.to_pdf(html, outdir / f"{plan.slug}.pdf", outdir)
    cover_out = render.to_cover_pdf(plan, outdir / "cover.pdf", outdir)
    epub_out = render.to_epub(plan, outdir / f"{plan.slug}.epub")
    preview = render.write_epub_preview(plan, outdir)
    outputs = {
        "pdf": str(pdf_out),
        "cover": str(cover_out),
        "epub": str(epub_out),
        "epub_preview": str(preview),
    }
    render.write_manifest(plan, outdir, outputs)
    return outputs
