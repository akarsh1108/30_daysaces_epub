"""Deterministic SVG diagrams. No model, no network, byte-identical on re-run.

Each kind draws a dimensioned schematic parameterised by the figure's values.
These are schematics, not room-accurate drawings: their job is to make a
number visible and memorable, with the minimum shown in red where one exists.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from ..plan import Figure

INK = "#111111"
ACCENT = "#1E5F6B"
MIN_RED = "#C8442B"
PAPER = "#FFFFFF"
TINT = "#E8EFF0"
MUTED = "#8A8F91"
FONT = "DejaVu Sans, Helvetica, Arial, sans-serif"

W, H = 900, 560


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _nums(v: str) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", v)]


def _unit(v: str) -> str:
    m = re.search(r"(inch(?:es)?|feet|foot|ft|cm|centimet\w*|mm|percent|%|lumens?)", v, re.I)
    if not m:
        return ""
    u = m.group(1).lower()
    return {"inches": "in", "inch": "in", "feet": "ft", "foot": "ft", "percent": "%",
            "centimeters": "cm", "centimetres": "cm"}.get(u, u)


def _range_label(v: str) -> str:
    n, u = _nums(v), _unit(v)
    if len(n) >= 2:
        return f"{n[0]:g}–{n[1]:g} {u}".strip()
    if n:
        return f"{n[0]:g} {u}".strip()
    return v


def _head(title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'font-family="{FONT}" fill="{INK}">'
        f'<rect width="{W}" height="{H}" fill="{PAPER}"/>'
        f'<text x="40" y="52" font-size="26" font-weight="700">{_esc(title)}</text>'
    )


def _dim_h(x1: float, x2: float, y: float, label: str, color: str = ACCENT) -> str:
    return (
        f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{color}" stroke-width="2"/>'
        f'<line x1="{x1}" y1="{y-8}" x2="{x1}" y2="{y+8}" stroke="{color}" stroke-width="2"/>'
        f'<line x1="{x2}" y1="{y-8}" x2="{x2}" y2="{y+8}" stroke="{color}" stroke-width="2"/>'
        f'<text x="{(x1+x2)/2}" y="{y-14}" font-size="18" text-anchor="middle" fill="{color}" font-weight="600">{_esc(label)}</text>'
    )


def _dim_v(x: float, y1: float, y2: float, label: str, color: str = ACCENT) -> str:
    return (
        f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2}" stroke="{color}" stroke-width="2"/>'
        f'<line x1="{x-8}" y1="{y1}" x2="{x+8}" y2="{y1}" stroke="{color}" stroke-width="2"/>'
        f'<line x1="{x-8}" y1="{y2}" x2="{x+8}" y2="{y2}" stroke="{color}" stroke-width="2"/>'
        f'<text x="{x+14}" y="{(y1+y2)/2+6}" font-size="18" fill="{color}" font-weight="600">{_esc(label)}</text>'
    )


def _foot(caption: str = "") -> str:
    # Caption is rendered by the book template under the figure; keep the SVG clean.
    return "</svg>"


# ---------------------------------------------------------------- kinds ---

def plan(fig: Figure) -> str:
    """Room outline, furniture block, circulation band with its width dimensioned."""
    s = _head(fig.title)
    rx, ry, rw, rh = 60, 120, 560, 360
    s += f'<rect x="{rx}" y="{ry}" width="{rw}" height="{rh}" fill="none" stroke="{INK}" stroke-width="3"/>'
    s += f'<rect x="{rx+40}" y="{ry+40}" width="260" height="110" fill="{TINT}" stroke="{ACCENT}" stroke-width="2"/>'
    s += f'<text x="{rx+170}" y="{ry+100}" font-size="18" text-anchor="middle" fill="{ACCENT}">seating</text>'
    s += f'<rect x="{rx+380}" y="{ry+40}" width="140" height="60" fill="{TINT}" stroke="{ACCENT}" stroke-width="2"/>'
    s += f'<text x="{rx+450}" y="{ry+77}" font-size="16" text-anchor="middle" fill="{ACCENT}">console</text>'
    # circulation band
    bx = rx + 330
    s += f'<rect x="{bx}" y="{ry+2}" width="44" height="{rh-4}" fill="{ACCENT}" opacity="0.14"/>'
    band = fig.values[0] if fig.values else ""
    s += _dim_h(bx, bx + 44, ry - 18, _range_label(band))
    if fig.minimum:
        s += f'<line x1="{bx+8}" y1="{ry+2}" x2="{bx+8}" y2="{ry+rh-2}" stroke="{MIN_RED}" stroke-width="2" stroke-dasharray="8 6"/>'
        s += f'<text x="{bx+8}" y="{ry+rh+34}" font-size="16" text-anchor="middle" fill="{MIN_RED}" font-weight="600">never below {_esc(_range_label(fig.minimum))}</text>'
    s += _legend(fig, 660, 110)
    return s + _foot(fig.caption)


def comparison(fig: Figure) -> str:
    """Two rug sizes side by side in the same room, border to wall dimensioned."""
    s = _head(fig.title)
    sizes = [v for v in fig.values if re.search(r"\d+\s*(?:by|x|×)\s*\d+", v, re.I)][:2] or fig.values[:2]
    border = next((v for v in fig.values if re.search(r"\d+\s*(?:to|-)\s*\d+\s*(?:inch|cm)", v, re.I)), None)
    for i, size in enumerate(sizes or ["A", "B"]):
        ox = 60 + i * 420
        s += f'<rect x="{ox}" y="90" width="380" height="330" fill="none" stroke="{INK}" stroke-width="3"/>'
        inset = 46 if i == 0 else 30
        s += f'<rect x="{ox+inset}" y="{90+inset}" width="{380-2*inset}" height="{330-2*inset}" fill="{TINT}" stroke="{ACCENT}" stroke-width="2"/>'
        s += f'<text x="{ox+190}" y="270" font-size="22" text-anchor="middle" fill="{ACCENT}" font-weight="700">{_esc(size)}</text>'
        s += f'<rect x="{ox+90}" y="{130}" width="200" height="70" fill="{PAPER}" stroke="{INK}" stroke-width="2"/>'
        s += f'<text x="{ox+190}" y="172" font-size="15" text-anchor="middle">sofa, front legs on rug</text>'
        if border:
            s += _dim_v(ox + 380 - 10, 90, 90 + inset, _range_label(border))
    s += f'<text x="250" y="450" font-size="17" text-anchor="middle" fill="{MUTED}">compact sofa, no chair</text>'
    s += f'<text x="670" y="450" font-size="17" text-anchor="middle" fill="{MUTED}">two-seat sofa + accent chair</text>'
    return s + _foot(fig.caption)


def elevation(fig: Figure) -> str:
    """Wall with floor line, an anchor object, and every value as a vertical dimension."""
    s = _head(fig.title)
    floor_y = 470
    s += f'<line x1="60" y1="{floor_y}" x2="840" y2="{floor_y}" stroke="{INK}" stroke-width="3"/>'
    s += f'<text x="60" y="{floor_y+26}" font-size="15" fill="{MUTED}">floor</text>'
    s += f'<rect x="120" y="330" width="300" height="140" fill="{TINT}" stroke="{ACCENT}" stroke-width="2"/>'
    s += f'<text x="270" y="410" font-size="17" text-anchor="middle" fill="{ACCENT}">sofa / bed / console</text>'
    s += f'<rect x="170" y="160" width="200" height="130" fill="none" stroke="{INK}" stroke-width="2"/>'
    s += f'<text x="270" y="232" font-size="15" text-anchor="middle" fill="{MUTED}">art / mirror / TV</text>'
    ys = [(290, 330), (160, floor_y), (100, 160)]
    for i, v in enumerate(fig.values[:3]):
        y1, y2 = ys[i]
        s += _dim_v(470 + i * 120, y1, y2, _range_label(v), MIN_RED if fig.minimum == v else ACCENT)
    return s + _foot(fig.caption)


def ratio_bar(fig: Figure) -> str:
    """Proportion bar. Handles 60-30-10 style ratios and single 'X percent of Y' rules."""
    s = _head(fig.title)
    ratio = next((v for v in fig.values if "ratio" in v), None)
    parts = [int(n) for n in re.findall(r"\d+", ratio)] if ratio else []
    if len(parts) >= 3:
        total = sum(parts)
        x, y, w = 60, 200, 780
        shades = [ACCENT, "#7FA7AE", MIN_RED]
        names = fig.labels or ["primary", "secondary", "accent"]
        for i, p in enumerate(parts[:3]):
            pw = w * p / total
            s += f'<rect x="{x}" y="{y}" width="{pw}" height="110" fill="{shades[i]}"/>'
            s += f'<text x="{x+pw/2}" y="{y+66}" font-size="26" text-anchor="middle" fill="{PAPER}" font-weight="700">{p}%</text>'
            s += f'<text x="{x+pw/2}" y="{y+150}" font-size="18" text-anchor="middle">{_esc(names[i] if i < len(names) else "")}</text>'
            x += pw
    else:
        v = fig.values[0] if fig.values else ""
        n = _nums(v)
        lo, hi = (n[0], n[1]) if len(n) >= 2 else (n[0], n[0]) if n else (0, 0)
        x, y, w = 60, 220, 780
        s += f'<rect x="{x}" y="{y}" width="{w}" height="90" fill="{TINT}"/>'
        s += f'<rect x="{x + w*lo/100}" y="{y}" width="{w*(hi-lo)/100 or 6}" height="90" fill="{ACCENT}"/>'
        s += f'<text x="{x}" y="{y+130}" font-size="18">0%</text><text x="{x+w}" y="{y+130}" font-size="18" text-anchor="end">100% of reference width</text>'
        s += f'<text x="{x + w*(lo+hi)/200}" y="{y+56}" font-size="24" text-anchor="middle" fill="{PAPER}" font-weight="700">{_range_label(v)}</text>'
    return s + _foot(fig.caption)


def chart(fig: Figure) -> str:
    """Donut for a budget split; falls back to a bar list for anything else."""
    s = _head(fig.title)
    pairs = []
    for lbl, v in zip(fig.labels or [], fig.values):
        n = _nums(v)
        if n:
            pairs.append((lbl, sum(n[:2]) / len(n[:2])))
    if not pairs:
        for v in fig.values:
            n = _nums(v)
            if n:
                pairs.append((v, sum(n[:2]) / len(n[:2])))
    total = sum(p for _, p in pairs) or 1
    import math
    cx, cy, r = 300, 300, 150
    a0 = -math.pi / 2
    shades = [ACCENT, "#7FA7AE", "#B8CFD3", MIN_RED, MUTED, "#D9C6B0"]
    for i, (lbl, p) in enumerate(pairs[:6]):
        a1 = a0 + 2 * math.pi * p / total
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        large = 1 if a1 - a0 > math.pi else 0
        s += (f'<path d="M{cx},{cy} L{x0:.1f},{y0:.1f} A{r},{r} 0 {large},1 {x1:.1f},{y1:.1f} Z" '
              f'fill="{shades[i % len(shades)]}"/>')
        s += f'<rect x="560" y="{110+i*52}" width="26" height="26" fill="{shades[i % len(shades)]}"/>'
        s += f'<text x="600" y="{130+i*52}" font-size="19">{_esc(lbl[:36])}  {p:g}%</text>'
        a0 = a1
    s += f'<circle cx="{cx}" cy="{cy}" r="70" fill="{PAPER}"/>'
    return s + _foot(fig.caption)


def table(fig: Figure) -> str:
    """Spec table as SVG so it renders identically in print and EPUB."""
    s = _head(fig.title)
    labels = list(fig.labels or [])
    if len(labels) < len(fig.values):
        labels.extend(v[:48] for v in fig.values[len(labels) :])
    rows = list(zip(labels, fig.values))
    y = 110
    s += f'<rect x="40" y="{y-32}" width="820" height="44" fill="{ACCENT}"/>'
    s += f'<text x="60" y="{y}" font-size="19" fill="{PAPER}" font-weight="700">Parameter</text>'
    s += f'<text x="560" y="{y}" font-size="19" fill="{PAPER}" font-weight="700">Value</text>'
    for i, (lbl, v) in enumerate(rows[:8]):
        yy = y + 52 + i * 48
        if i % 2 == 0:
            s += f'<rect x="40" y="{yy-30}" width="820" height="48" fill="{TINT}"/>'
        color = MIN_RED if fig.minimum == v else INK
        s += f'<text x="60" y="{yy}" font-size="18">{_esc(lbl[:48])}</text>'
        s += f'<text x="560" y="{yy}" font-size="18" fill="{color}" font-weight="600">{_esc(_range_label(v))}</text>'
    return s + _foot(fig.caption)


def _legend(fig: Figure, x: int, y: int) -> str:
    out = f'<text x="{x}" y="{y}" font-size="17" font-weight="700">Values</text>'
    for i, v in enumerate(fig.values[:5]):
        out += f'<text x="{x}" y="{y+30+i*28}" font-size="17" fill="{ACCENT}">{_esc(_range_label(v))}</text>'
    return out


RENDERERS = {
    "plan": plan, "comparison": comparison, "elevation": elevation,
    "ratio_bar": ratio_bar, "chart": chart, "table": table,
}


def render(fig: Figure, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    fn = RENDERERS.get(fig.kind, table)
    svg = fn(fig)
    name = f"{fig.spec_id or re.sub(r'[^a-z0-9]+', '-', fig.title.lower())[:40]}.svg"
    path = outdir / name
    path.write_text(svg, encoding="utf-8")
    return path
