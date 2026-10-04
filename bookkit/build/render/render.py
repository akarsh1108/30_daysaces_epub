"""BookPlan -> HTML -> PDF (6x9, WeasyPrint) and EPUB3 (ebooklib)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import Settings
from ..plan import BookPlan

HERE = Path(__file__).parent
TEMPLATES = HERE / "templates"
FONTS = HERE / "fonts"


def _font_face_css() -> str:
    """Absolute file URLs so WeasyPrint can embed magazine typefaces."""
    faces = [
        ("Source Serif 4", 400, "SourceSerif4-400.woff"),
        ("Source Serif 4", 700, "SourceSerif4-700.woff"),
        ("Fraunces", 600, "Fraunces-600.woff"),
        ("Fraunces", 700, "Fraunces-700.woff"),
        ("DM Sans", 400, "DMSans-400.woff"),
        ("DM Sans", 700, "DMSans-700.woff"),
    ]
    rules: list[str] = []
    for family, weight, name in faces:
        path = FONTS / name
        if not path.exists():
            continue
        rules.append(
            f'@font-face{{font-family:"{family}";font-weight:{weight};'
            f'font-style:normal;src:url("{path.resolve().as_uri()}") format("woff");}}'
        )
    return "\n".join(rules)


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _credits(plan: BookPlan) -> list[str]:
    seen: list[str] = []
    for p in plan.all_photos:
        if p.credit and p.credit not in seen and p.source == "pexels":
            seen.append(p.credit)
    return seen


def to_html(plan: BookPlan, base_dir: Path) -> str:
    """Render HTML with image paths made relative to base_dir."""
    env = _env()
    css = _font_face_css() + "\n" + (TEMPLATES / "book.css").read_text(encoding="utf-8").replace(
        "{{ accent }}", plan.accent_hex
    )

    def rel(p: str | None) -> str | None:
        if not p:
            return None
        try:
            return str(Path(p).resolve().relative_to(base_dir.resolve())).replace("\\", "/")
        except ValueError:
            return Path(p).resolve().as_uri()

    plan_view = plan.model_copy(deep=True)
    if plan_view.cover:
        plan_view.cover.path = rel(plan_view.cover.path)
    for ch in plan_view.chapters:
        if ch.opener:
            ch.opener.path = rel(ch.opener.path)
        for s in ch.sections:
            if s.hero:
                s.hero.path = rel(s.hero.path)
            for f in s.figures:
                f.path = rel(f.path)
            for ph in s.inline_photos:
                ph.path = rel(ph.path)

    return env.get_template("book.html.j2").render(
        plan=plan_view, css=css, year=dt.date.today().year, credits=_credits(plan)
    )


def to_pdf(html: str, out: Path, base_dir: Path) -> Path:
    from weasyprint import HTML

    out.parent.mkdir(parents=True, exist_ok=True)
    candidates = [out, out.with_name(out.stem + ".new.pdf"), out.with_name(out.stem + ".revised.pdf")]
    last_err: PermissionError | None = None
    for dest in candidates:
        try:
            HTML(string=html, base_url=str(base_dir)).write_pdf(str(dest))
            if dest != out:
                print(f"  pdf locked; wrote {dest.name} instead")
            return dest
        except PermissionError as err:
            last_err = err
    raise last_err or PermissionError(str(out))


def to_cover_pdf(plan: BookPlan, out: Path, base_dir: Path) -> Path:
    """Standalone 6x9 front cover for KDP ebook / social — same shared template."""
    from weasyprint import HTML

    env = _env()
    css = _font_face_css() + "\n" + (TEMPLATES / "book.css").read_text(encoding="utf-8").replace(
        "{{ accent }}", plan.accent_hex
    )

    def rel(p: str | None) -> str | None:
        if not p:
            return None
        try:
            return str(Path(p).resolve().relative_to(base_dir.resolve())).replace("\\", "/")
        except ValueError:
            return Path(p).resolve().as_uri()

    cover = plan.cover
    img = rel(cover.path) if cover else None
    tagline = plan.tagline or plan.subtitle or ""
    if getattr(plan, "designed_cover", False) and img:
        body = f"""
<section class="cover cover-designed">
  <img class="cover-photo" src="{img}" alt=""/>
</section>
"""
    else:
        body = f"""
<section class="cover">
  {"<img class='cover-photo' src='" + img + "' alt=''/>" if img else "<div class='cover-photo'></div>"}
  <div class="cover-panel">
    <p class="series">{plan.series or "Home Decor Series"}</p>
    <div class="rule"></div>
    <h1>{plan.title}</h1>
    {f'<p class="tagline">{tagline}</p>' if tagline else ""}
    {f'<p class="author">{plan.author}</p>' if plan.author else ""}
  </div>
</section>
"""
    html = f"<!DOCTYPE html><html><head><meta charset='utf-8'/><style>{css}</style></head><body>{body}</body></html>"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        HTML(string=html, base_url=str(base_dir)).write_pdf(str(out))
        return out
    except PermissionError:
        alt = out.with_name(out.stem + ".new.pdf")
        HTML(string=html, base_url=str(base_dir)).write_pdf(str(alt))
        print(f"  cover locked; wrote {alt.name} instead")
        return alt


def to_epub(plan: BookPlan, out: Path) -> Path:
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier(plan.slug)
    book.set_title(plan.title)
    book.set_language("en")
    if plan.author:
        book.add_author(plan.author)
    if plan.cover and plan.cover.path:
        cover_file = Path(plan.cover.path)
        if cover_file.is_file():
            book.set_cover("cover.jpg", cover_file.read_bytes())

    css = epub.EpubItem(
        uid="style",
        file_name="style.css",
        media_type="text/css",
        content=_epub_css(plan.accent_hex).encode("utf-8"),
    )
    book.add_item(css)

    items: list = []
    added_images: set[str] = set()
    used_names: set[str] = set()

    def add_img(path: str | None) -> str | None:
        """Embed image; convert SVG diagrams to PNG for Kindle/reader support."""
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        cache_key = str(p.resolve())
        if cache_key in added_images:
            # Return the previously chosen epub filename
            if p.suffix.lower() == ".svg":
                return f"{p.stem}.png"
            return p.name

        if p.suffix.lower() == ".svg":
            raw, name, media = _svg_to_png_bytes(p), f"{p.stem}.png", "image/png"
        else:
            raw, name, media = p.read_bytes(), p.name, "image/jpeg"

        uid = p.stem
        n = 1
        while uid in used_names:
            n += 1
            uid = f"{p.stem}-{n}"
        used_names.add(uid)

        book.add_item(epub.EpubItem(uid=uid, file_name=f"img/{name}", media_type=media, content=raw))
        added_images.add(cache_key)
        return name

    section_n = 0
    for ch in plan.chapters:
        # Chapter opener as its own full-width page when present
        if ch.opener and ch.opener.path:
            section_n += 1
            opener_name = add_img(ch.opener.path)
            opener_html = [
                '<div class="opener">',
                f'<img class="full" src="img/{opener_name}" alt="{_esc(ch.opener.alt)}"/>' if opener_name else "",
                f'<p class="kicker">Chapter</p><h1>{_esc(ch.title)}</h1>',
                f'<p class="intro">{_esc(ch.intro)}</p>' if ch.intro else "",
                "</div>",
            ]
            item = epub.EpubHtml(
                title=ch.title,
                file_name=f"ch-{section_n:03d}-opener.xhtml",
                lang="en",
            )
            item.content = _epub_doc(ch.title, "\n".join(p for p in opener_html if p))
            item.add_item(css)
            book.add_item(item)
            items.append(item)

        for s in ch.sections:
            section_n += 1
            parts: list[str] = ['<article class="unit">']
            if s.hero and s.hero.path:
                hero = add_img(s.hero.path)
                if hero:
                    parts.append(
                        f'<div class="hero"><img class="full" src="img/{hero}" alt="{_esc(s.hero.alt)}"/>'
                        f'<div class="hero-cap">'
                        f'<p class="day">{_esc(s.day_label)}</p>'
                        f"<h2>{_esc(s.title)}</h2></div></div>"
                    )
                else:
                    parts.append(f'<p class="day">{_esc(s.day_label)}</p><h2>{_esc(s.title)}</h2>')
            else:
                if not (ch.opener and s is ch.sections[0]):
                    parts.append(f"<h2>{_esc(ch.title)}</h2>")
                parts.append(f'<p class="day">{_esc(s.day_label)}</p><h2>{_esc(s.title)}</h2>')

            if s.scenario:
                parts.append(
                    f'<blockquote class="scenario"><p class="label">In this apartment</p>'
                    f"<p>{_esc(s.scenario)}</p></blockquote>"
                )
            if s.principle:
                parts.append(f'<p class="principle">{_esc(s.principle)}</p>')

            for i, para in enumerate(s.body):
                parts.append(f"<p>{_esc(para)}</p>")
                if i < len(s.figures) and s.figures[i].path:
                    f = s.figures[i]
                    name = add_img(f.path)
                    if name:
                        parts.append(
                            f'<figure class="diagram"><img class="full" src="img/{name}" alt="{_esc(f.title)}"/>'
                            f"<figcaption>{_esc(f.caption)}</figcaption></figure>"
                        )
            for f in s.figures[len(s.body) :]:
                if f.path:
                    name = add_img(f.path)
                    if name:
                        parts.append(
                            f'<figure class="diagram"><img class="full" src="img/{name}" alt="{_esc(f.title)}"/>'
                            f"<figcaption>{_esc(f.caption)}</figcaption></figure>"
                        )
            for ph in s.inline_photos:
                name = add_img(ph.path)
                if name:
                    cap = ph.alt or ph.query
                    parts.append(
                        f'<figure class="diagram"><img class="full" src="img/{name}" alt="{_esc(cap)}"/>'
                        f"<figcaption>{_esc(cap)}</figcaption></figure>"
                    )

            if s.steps:
                parts.append(
                    f'<div class="steps"><h3>{_esc(s.steps_label or "Do this")}</h3><ol>'
                    + "".join(f"<li>{_esc(st.text)}</li>" for st in s.steps)
                    + "</ol></div>"
                )
            if s.mistake:
                parts.append(
                    f'<aside class="mistake"><h3>Where this goes wrong</h3>'
                    f"<p>{_esc(s.mistake)}</p></aside>"
                )
            if s.checklist:
                parts.append(
                    f'<div class="checklist"><h3>{_esc(s.checklist_label or "Before you move on")}</h3><ul>'
                    + "".join(f"<li>{_esc(c)}</li>" for c in s.checklist)
                    + "</ul></div>"
                )
            parts.append("</article>")

            item = epub.EpubHtml(
                title=s.title,
                file_name=f"ch-{section_n:03d}-{s.slug[:40] or 'section'}.xhtml",
                lang="en",
            )
            item.content = _epub_doc(s.title, "\n".join(parts))
            item.add_item(css)
            book.add_item(item)
            items.append(item)

    book.toc = tuple(items)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    spine = ["nav", *items]
    if plan.cover and plan.cover.path and Path(plan.cover.path).is_file():
        spine = ["cover", *spine]
    book.spine = spine
    out.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(out), book)
    return out


def write_epub_preview(plan: BookPlan, outdir: Path) -> Path:
    """Browser-viewable mirror of the EPUB layout (open in Chrome)."""
    accent = plan.accent_hex
    css = _epub_css(accent) + """
html { background: #eceff0; }
body.preview-shell { margin: 0; padding: 24px 12px 48px; }
.device {
  max-width: 420px;
  margin: 0 auto 28px;
  background: #fff;
  border: 1px solid #cfd5d7;
  border-radius: 12px;
  overflow: hidden;
  padding: 0 0 20px;
}
.device .label {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #7a8285;
  padding: 10px 16px;
  border-bottom: 1px solid #e8ecec;
  background: #f7f9f9;
}
.device .page { padding: 0 16px 8px; }
.preview-top {
  max-width: 420px;
  margin: 0 auto 16px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #333;
}
.preview-top h1 { font-size: 20px; margin: 0 0 6px; }
.preview-top p { margin: 0; font-size: 13px; color: #666; }
"""
    chunks: list[str] = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'/>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'/>",
        f"<title>EPUB preview — {plan.title}</title>",
        f"<style>{css}</style></head>",
        "<body class='preview-shell'>",
        "<div class='preview-top'>",
        f"<h1>{_esc(plan.title)}</h1>",
        "<p>Kindle-style preview of the EPUB. Scroll through each section below.</p>",
        "</div>",
    ]

    def rel(path: str | None) -> str | None:
        if not path:
            return None
        try:
            return str(Path(path).resolve().relative_to(outdir.resolve())).replace("\\", "/")
        except ValueError:
            return Path(path).resolve().as_uri()

    if plan.cover and plan.cover.path:
        cover_src = rel(plan.cover.path)
        if cover_src:
            chunks.append("<div class='device'><div class='label'>Cover</div><div class='page'>")
            chunks.append(f"<img class='full' src='{cover_src}' alt='{_esc(plan.cover.alt or plan.title)}'/>")
            chunks.append("</div></div>")

    for ch in plan.chapters:
        if ch.opener and ch.opener.path:
            src = rel(ch.opener.path)
            chunks.append("<div class='device'><div class='label'>Chapter opener</div><div class='page'>")
            chunks.append("<div class='opener'>")
            if src:
                chunks.append(f"<img class='full' src='{src}' alt='{_esc(ch.opener.alt)}'/>")
            chunks.append(f"<p class='kicker'>Chapter</p><h1>{_esc(ch.title)}</h1>")
            if ch.intro:
                chunks.append(f"<p class='intro'>{_esc(ch.intro)}</p>")
            chunks.append("</div></div></div>")

        for s in ch.sections:
            chunks.append(
                f"<div class='device'><div class='label'>{_esc(s.day_label or 'Section')} · {_esc(s.title)}</div>"
                "<div class='page'><article class='unit'>"
            )
            if s.hero and s.hero.path:
                src = rel(s.hero.path)
                chunks.append("<div class='hero'>")
                if src:
                    chunks.append(f"<img class='full' src='{src}' alt='{_esc(s.hero.alt)}'/>")
                chunks.append(
                    f"<div class='hero-cap'><p class='day'>{_esc(s.day_label)}</p>"
                    f"<h2>{_esc(s.title)}</h2></div></div>"
                )
            else:
                chunks.append(f"<p class='day'>{_esc(s.day_label)}</p><h2>{_esc(s.title)}</h2>")

            if s.scenario:
                chunks.append(
                    f"<blockquote class='scenario'><p class='label'>In this apartment</p>"
                    f"<p>{_esc(s.scenario)}</p></blockquote>"
                )
            if s.principle:
                chunks.append(f"<p class='principle'>{_esc(s.principle)}</p>")

            for i, para in enumerate(s.body):
                chunks.append(f"<p>{_esc(para)}</p>")
                if i < len(s.figures) and s.figures[i].path:
                    f = s.figures[i]
                    src = rel(f.path)
                    if src:
                        chunks.append(
                            f"<figure class='diagram'><img class='full' src='{src}' alt='{_esc(f.title)}'/>"
                            f"<figcaption>{_esc(f.caption)}</figcaption></figure>"
                        )
            for f in s.figures[len(s.body) :]:
                src = rel(f.path)
                if src:
                    chunks.append(
                        f"<figure class='diagram'><img class='full' src='{src}' alt='{_esc(f.title)}'/>"
                        f"<figcaption>{_esc(f.caption)}</figcaption></figure>"
                    )
            for ph in s.inline_photos:
                src = rel(ph.path)
                if src:
                    cap = ph.alt or ph.query
                    chunks.append(
                        f"<figure class='diagram'><img class='full' src='{src}' alt='{_esc(cap)}'/>"
                        f"<figcaption>{_esc(cap)}</figcaption></figure>"
                    )

            if s.steps:
                chunks.append(
                    f"<div class='steps'><h3>{_esc(s.steps_label or 'Do this')}</h3><ol>"
                    + "".join(f"<li>{_esc(st.text)}</li>" for st in s.steps)
                    + "</ol></div>"
                )
            if s.mistake:
                chunks.append(
                    f"<aside class='mistake'><h3>Where this goes wrong</h3>"
                    f"<p>{_esc(s.mistake)}</p></aside>"
                )
            if s.checklist:
                chunks.append(
                    f"<div class='checklist'><h3>{_esc(s.checklist_label or 'Before you move on')}</h3><ul>"
                    + "".join(f"<li>{_esc(c)}</li>" for c in s.checklist)
                    + "</ul></div>"
                )
            chunks.append("</article></div></div>")

    chunks.append("</body></html>")
    path = outdir / "epub-preview.html"
    path.write_text("\n".join(chunks), encoding="utf-8")
    return path


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _epub_doc(title: str, body: str) -> bytes:
    # ebooklib/lxml expect a normal HTML document (no XML declaration).
    return (
        f'<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" lang="en">\n'
        f'<head><title>{_esc(title)}</title>\n'
        f'<meta charset="utf-8"/>\n'
        f'<link rel="stylesheet" type="text/css" href="style.css"/>\n'
        f'</head>\n<body>\n{body}\n</body></html>'
    ).encode("utf-8")



def _svg_to_png_bytes(svg_path: Path) -> bytes:
    """Rasterize SVG to PNG for Kindle, which does not draw SVG.

    WeasyPrint 60+ dropped write_png; cairosvg is optional. PyMuPDF (already a
    dependency) opens SVG, or we print SVG→PDF with WeasyPrint then rasterize.
    """
    import io

    import pymupdf

    target_w = 1200

    try:
        doc = pymupdf.open(svg_path)
        try:
            page = doc[0]
            zoom = target_w / page.rect.width if page.rect.width else 2.0
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            return pix.tobytes("png")
        finally:
            doc.close()
    except Exception:
        pass

    try:
        from weasyprint import HTML

        html = (
            "<html><body style='margin:0'>"
            f"<img src=\"{svg_path.resolve().as_uri()}\" "
            f"style='width:{target_w}px;height:auto;display:block'/>"
            "</body></html>"
        )
        pdf_bytes = HTML(string=html).write_pdf()
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            return pix.tobytes("png")
        finally:
            doc.close()
    except Exception:
        pass

    try:
        import cairosvg  # type: ignore

        return cairosvg.svg2png(url=str(svg_path), output_width=target_w)
    except Exception:
        pass

    from PIL import Image, ImageDraw
    import re

    text = svg_path.read_text(encoding="utf-8", errors="ignore")
    title = "Diagram"
    m = re.search(r"<text[^>]*font-weight=\"700\"[^>]*>([^<]+)</text>", text)
    if m:
        title = m.group(1)[:80]
    img = Image.new("RGB", (1200, 750), (243, 246, 246))
    d = ImageDraw.Draw(img)
    d.rectangle([24, 24, 1176, 726], outline=(30, 95, 107), width=3)
    d.text((48, 48), title, fill=(20, 24, 24))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _epub_css(accent: str) -> str:
    return f"""
@namespace epub "http://www.idpf.org/2007/ops";
html, body {{
  margin: 0;
  padding: 0;
  font-family: Georgia, "Times New Roman", serif;
  line-height: 1.55;
  color: #141816;
  background: #ffffff;
}}
h1, h2, h3 {{
  font-family: Georgia, "Times New Roman", serif;
  line-height: 1.2;
  page-break-after: avoid;
}}
h1 {{ font-size: 1.6em; margin: 0.4em 0 0.35em; }}
h2 {{ font-size: 1.35em; margin: 0.2em 0 0.45em; }}
h3 {{
  font-family: Helvetica, Arial, sans-serif;
  font-size: 0.78em;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin: 1.1em 0 0.45em;
}}
p {{ margin: 0 0 0.85em; }}
img.full, .hero img, .opener img, figure img {{
  display: block;
  width: 100% !important;
  max-width: 100% !important;
  height: auto !important;
  margin: 0;
  border: 0;
}}
.hero, .opener {{
  margin: 0 0 1em;
}}
.hero-cap {{
  padding: 0.55em 0 0.2em;
}}
.day, .kicker, .scenario .label {{
  font-family: Helvetica, Arial, sans-serif;
  font-size: 0.72em;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: {accent};
  margin: 0 0 0.35em;
}}
.intro {{ font-size: 0.95em; color: #333; }}
.scenario {{
  margin: 0.8em 0 1em;
  padding: 0.7em 0.8em;
  background: #f3f6f6;
  border-top: 3px solid {accent};
}}
.scenario p {{ margin: 0; font-style: italic; }}
.scenario .label {{ font-style: normal; margin-bottom: 0.4em; }}
.principle {{
  font-size: 1.12em;
  font-weight: bold;
  color: #0f3d45;
  border-left: 3px solid {accent};
  padding: 0.15em 0 0.15em 0.7em;
  margin: 0.4em 0 1em;
}}
figure.diagram {{
  margin: 1em 0;
  padding: 0.5em;
  background: #f3f6f6;
}}
figcaption {{
  font-family: Helvetica, Arial, sans-serif;
  font-size: 0.78em;
  color: #7a8285;
  margin-top: 0.4em;
}}
.steps {{
  background: #f3f6f6;
  padding: 0.7em 0.9em;
  margin: 1em 0;
}}
.steps ol {{ margin: 0; padding-left: 1.2em; }}
.steps li {{ margin: 0 0 0.4em; }}
.mistake {{
  background: #f8eeeb;
  border-left: 3px solid #c8442b;
  padding: 0.7em 0.9em;
  margin: 1em 0;
}}
.mistake h3 {{ color: #c8442b; }}
.checklist ul {{ list-style: none; padding: 0; margin: 0; }}
.checklist li {{
  padding-left: 1.3em;
  position: relative;
  margin: 0 0 0.4em;
}}
.checklist li::before {{
  content: "";
  position: absolute;
  left: 0;
  top: 0.25em;
  width: 0.7em;
  height: 0.7em;
  border: 1.5px solid {accent};
}}
"""


def size_guard(path: Path, cfg: Settings) -> tuple[float, bool]:
    mb = path.stat().st_size / 1_048_576
    return round(mb, 2), mb <= cfg.max_book_mb


def write_manifest(plan: BookPlan, outdir: Path, outputs: dict[str, str]) -> Path:
    manifest = {
        "title": plan.title,
        "units": plan.units,
        "outputs": outputs,
        "ai_generated_images": plan.ai_image_count,
        "kdp_ai_disclosure_required": plan.ai_image_count > 0,
        "images": [
            {"query": p.query, "source": p.source, "credit": p.credit, "ai": p.ai_generated, "path": p.path}
            for p in plan.all_photos
        ],
        "figures": [{"spec_id": f.spec_id, "kind": f.kind, "title": f.title, "path": f.path} for f in plan.all_figures],
    }
    path = outdir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
