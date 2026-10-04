# bookkit

Audits a published book PDF and emits a **page-anchored revision worklist**: what
is wrong, on which page, and exactly which diagram to draw where.

Built for a decor/how-to series where the content is good but the packaging is not.
The core premise: a design how-to already contains its own figure list, written as
sentences. Every measurement and named framework in the prose is a visual the
author described instead of drawing. This finds them.

Everything here is deterministic. No API keys, no LLM, no network.

---

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
# one book
python -m bookkit.cli audit books/30-day-1-bhk.pdf --out out

# whole series
python -m bookkit.cli audit books/*.pdf --out out --max-diagrams 45
```

Per book you get:

| File | Contents |
|---|---|
| `out/<slug>.worklist.md` | Human worklist: findings, framework table, diagram plan, render hints |
| `out/<slug>.audit.json` | Same data structured, for the render stage |

---

## The steps, per book

1. **Extract** — `extract.py` parses the PDF into blocks with page, font size and
   list-marker flags, then infers heading levels from the size histogram. Wrapped
   headings get merged. Ligatures normalised (PDFs encode `fi` as one glyph and it
   breaks every keyword match downstream).
2. **Audit formatting** — `audit/rules.py`. Trim size, heading levels, missing
   outline, fallback fonts, body point size, image density, front-matter length vs
   the Look Inside window, unbroken-prose pages, file size vs KDP delivery fee.
3. **Audit consistency** — `audit/consistency.py`. Per-chapter imperial/metric
   tallies to catch unit switching, and range-rule matching to catch the same
   guideline stated with two different numbers.
4. **Extract diagram specs** — `specs/diagrams.py`. Rejoins wrapped lines into
   sentences, pulls every measurement/ratio, scores each, dedupes restatements by
   topic, classifies into plan / elevation / comparison / ratio_bar / chart / table,
   and anchors to a page.
5. **Extract frameworks** — author-coined terms (`X Rule`, `X Grid`, `X Formula`).
   Each is a brandable asset that needs one signature diagram plus a repeating badge.
6. **Report** — `report.py` writes the Markdown worklist and the JSON.
7. **You act on it** — fix blockers, draw the ranked diagram list, rebuild.

## Scoring

Diagram candidates are ranked so you build the useful ones first:

| Signal | Weight |
|---|---|
| Each measurement in the sentence | +10 |
| Contains an author-coined framework name | +25 |
| Prescriptive wording (formula, rule, minimum, never) | +15 |
| States a range rather than one number | +10 |
| Sentence sits in a recap/takeaway section | -30 |

Same-topic restatements collapse to the highest scorer, so a rule taught on p42 and
repeated in Key Takeaways on p51 yields one diagram, not two.

---

## Rule reference

| ID | Severity | Catches |
|---|---|---|
| FMT001 | blocker | Page size is not a KDP trim |
| FMT002 | blocker | Fewer than 3 heading levels |
| FMT003 | blocker | No TOC / PDF outline |
| FMT004 | major | Body set in a fallback font |
| FMT005 | major | Body point size >= 14 |
| FMT006 | major | Pages of unbroken prose, no list or callout |
| VIS001 | blocker | Under 0.25 images per page |
| CNV001 | blocker | Front matter eats the Look Inside preview |
| ROY001 | major | File size vs $0.15/MB delivery fee |
| CON001 | major | Measurement units switch between chapters |
| CON002 | major | Same rule stated with contradictory numbers |

## Adding a rule

Write a function taking `Document`, returning `list[Finding]`, append it to
`ALL_RULES`. Add a fixture case in `tests/test_pipeline.py`.

```bash
python -m pytest tests/ -q
```

Tests build synthetic PDFs at runtime, so the suite needs no real book.

---

---

# Build stage

Turns the audited PDF into a 6x9 magazine-style interior (PDF + EPUB3) with
scenarios, dimensioned diagrams, and photographs.

```bash
cp .env.example .env          # fill in keys; PEXELS/FAL/MEDIA_COOLDOWN are shared with the reel pipeline
python -m bookkit.cli build books/30-day-1-bhk.pdf \
    --title "30-Day 1 BHK Aesthetic Transformation" \
    --author "Akarsh Priyadarshan" --series "30 Day Spaces" \
    --out build
```

Outputs in `build/<slug>/`:

| File | What |
|---|---|
| `<slug>.pdf` | 6x9 print interior, WeasyPrint |
| `<slug>.epub` | Reflowable Kindle upload |
| `plan.json` | The whole book as structured data. Edit and `rerender`. |
| `figures/*.svg` | Every diagram, deterministic |
| `manifest.json` | Image provenance, AI count, delivery-fee check |
| `book.html` | For eyeballing in a browser |

## The steps, per book

1. **Audit** — same as above. Produces the ranked diagram specs.
2. **Plan** — restructure into `BookPlan`: chapters → sections, each with
   scenario / principle / body / figures / steps / mistake / checklist / hero.
   - `--planner openai` (default when `OPENAI_API_KEY` is set): one strict-JSON
     call per chapter. Writes scenarios about named residents in real Indian
     apartments, splits prose into blocks, picks figures from the audited specs
     by id, writes photo queries and Flux prompts. Told not to change any number.
     `verify_numbers()` diffs every measurement in source vs. output; a chapter
     that drops more than ~10% is retried once, then falls back to heuristic.
   - `--planner heuristic`: offline. Splits on the author's own markers
     (The Problem / Deep Dive / Key Takeaways / Action Items), attaches figures by
     page. No scenarios. Exists so the pipeline runs without keys.
3. **Media**
   - Diagrams: `media/svg.py` renders each figure kind (plan, elevation,
     comparison, ratio_bar, chart, table) from its values. A floor value is
     drawn in red. Byte-identical on re-run.
   - Photos: `media/photos.py` tries **Pexels** first (free, commercial-use
     licence, real interiors), **Flux via fal.ai** second, placeholder last.
     Cached by query hash. `MEDIA_COOLDOWN` honoured. Every image is downsampled
     to 1000px wide JPEG before it enters the book.
   - `--media mock` skips all network and draws placeholders.
4. **Render** — Jinja2 → HTML → WeasyPrint PDF at 6x9 with running page numbers
   and a linked TOC; ebooklib EPUB3 with the same content.
5. **Guard** — file size vs `MAX_BOOK_MB`; AI image count for the KDP
   disclosure; manifest written.

## Editing the plan by hand

`plan.json` is the source of truth. Fix a scenario, swap a photo query, drop a
figure, reorder steps — then:

```bash
python -m bookkit.cli rerender build/<slug>/plan.json --out build/<slug>
```

No audit, no API calls.

## Design decisions baked into the template

- One serif for reading (falls back to DejaVu Serif; set Literata or Source
  Serif 4 in `book.css` for production), one sans for labels and dimensions.
- One accent (`accent_hex` in the plan, default teal). Red only for minimums.
- Every section opens on a full-width photo with the day label and title;
  scenario is set off with a rule; principle is the one bold line; diagrams run
  text-width; steps, mistake box and checklist close the unit.
- Chapter openers are full bleed with a tinted title panel.

## Where the photos come from, and the disclosure

Pexels photos are free for commercial use and credited on the copyright page.
Flux images are flagged `ai_generated` in the manifest and counted into the
copyright-page disclosure line. KDP requires you to declare AI-generated
content at publish time; the manifest gives you the number.

## Publishing

KDP has no public upload API. Everything to the final file automates; the
upload and metadata edit stay manual, 22 times.
