from .extract import extract
from .models import AuditReport, Document, Finding, DiagramSpec

__all__ = ["extract", "AuditReport", "Document", "Finding", "DiagramSpec", "audit_book"]


def audit_book(pdf_path, max_diagrams: int = 45):
    """Run the full pipeline on one PDF and return (report, frameworks)."""
    from .audit.consistency import run_consistency
    from .audit.rules import run_rules
    from .specs.diagrams import extract_diagrams, extract_frameworks

    doc = extract(pdf_path)
    findings = run_rules(doc) + run_consistency(doc)
    order = {"blocker": 0, "major": 1, "minor": 2}
    findings.sort(key=lambda f: (order[f.severity], f.rule_id))
    diagrams = extract_diagrams(doc, limit=max_diagrams)
    return AuditReport(doc, findings, diagrams), extract_frameworks(doc)
