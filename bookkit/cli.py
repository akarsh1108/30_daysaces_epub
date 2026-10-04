"""CLI: bookkit audit <pdf...> [--out DIR]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import audit_book
from .report import write


def main(argv: list[str] | None = None) -> int:
    # Load .env early so WEASYPRINT_DLL_DIRECTORIES is set before PDF render.
    from .build.config import load_dotenv, settings as load_settings

    load_dotenv()
    cfg_boot = load_settings()
    # settings() already read WEASYPRINT into os.environ via load_dotenv
    _ = cfg_boot

    p = argparse.ArgumentParser(prog="bookkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="Audit one or more book PDFs")
    a.add_argument("pdfs", nargs="+", type=Path)
    a.add_argument("--out", type=Path, default=Path("out"))
    a.add_argument("--max-diagrams", type=int, default=45)

    b = sub.add_parser("build", help="Audit, plan, fetch media and render a book")
    b.add_argument("pdf", type=Path)
    b.add_argument("--title", required=True)
    b.add_argument("--author", default="")
    b.add_argument("--series", default="")
    b.add_argument("--out", type=Path, default=Path("build"))
    b.add_argument("--planner", choices=["auto", "openai", "heuristic"], default="auto")
    b.add_argument("--media", choices=["live", "mock"], default="live")
    b.add_argument("--max-diagrams", type=int, default=45)

    r = sub.add_parser("rerender", help="Re-render from an edited plan.json")
    r.add_argument("plan", type=Path)
    r.add_argument("--out", type=Path, default=Path("build"))

    args = p.parse_args(argv)

    if args.cmd == "build":
        from .build.pipeline import build
        outputs = build(args.pdf, args.title, args.out / args.pdf.stem, args.author, args.series,
                        args.planner, args.media, max_diagrams=args.max_diagrams)
        print("\n".join(f"{k}: {v}" for k, v in outputs.items()))
        return 0

    if args.cmd == "rerender":
        from .build.pipeline import rebuild_from_plan
        outputs = rebuild_from_plan(args.plan, args.out)
        print("\n".join(f"{k}: {v}" for k, v in outputs.items()))
        return 0

    for pdf in args.pdfs:
        if not pdf.exists():
            print(f"skip (not found): {pdf}", file=sys.stderr)
            continue
        report, frameworks = audit_book(pdf, max_diagrams=args.max_diagrams)
        paths = write(report, frameworks, args.out)
        blockers = sum(1 for f in report.findings if f.severity == "blocker")
        print(
            f"{pdf.name}: {len(report.findings)} findings ({blockers} blockers), "
            f"{len(report.diagrams)} diagrams -> {paths['markdown']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
