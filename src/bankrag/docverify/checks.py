"""Consistency checks and PDF forensics. Each returns Findings with a stable code; rules.py turns codes into
decisions. A check never decides anything itself."""
from __future__ import annotations

import calendar
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from . import names, nric, statutory
from .extract import PdfFacts, _DATE_SPAN, _rows

# code -> (source, severity, meaning). severity "info" findings are recorded but need no rule.
CODES = {
    "arithmetic_mismatch": ("consistency", "high", "totals or running balances do not add up"),
    "statutory_mismatch": ("consistency", "medium", "EPF/SOCSO/EIS outside the (simplified) statutory rates"),
    "nric_invalid": ("consistency", "high", "NRIC is not a possible MyKad number"),
    "nric_mismatch": ("consistency", "high", "NRIC on the document differs from the application"),
    "name_mismatch": ("consistency", "high", "name on the document is a different person"),
    "name_partial_match": ("consistency", "medium", "name only partly matches the application"),
    "date_outside_period": ("consistency", "high", "dates inside the document contradict its period"),
    "document_stale": ("consistency", "medium", "document period is older than policy allows"),
    "document_future_dated": ("consistency", "high", "document dated after the application"),
    "pdf_editor_producer": ("forensics", "medium", "file was last saved by a PDF editing tool"),
    "pdf_incremental_update": ("forensics", "medium", "file was changed after it was first saved (incremental update)"),
    "pdf_rewritten_after_creation": ("forensics", "medium", "re-written by different software days after creation"),
    "pdf_modified_after_creation": ("forensics", "info", "modified days after creation by the same software (e.g. re-download)"),
    "font_inconsistent": ("forensics", "medium", "amounts are not all in the same font family and size"),
    "anomaly_score_high": ("anomaly", "medium", "figures are unusual compared with genuine documents"),
    "duplicate_other_applicant": ("registry", "high", "same document already submitted by a different applicant"),
    "resubmitted_same_applicant": ("registry", "info", "same applicant submitted this document before"),
    "unreadable_pdf": ("extraction", "medium", "file could not be read as a PDF"),
    "unsupported_document": ("extraction", "medium", "not a recognised payslip or bank statement (or no text layer)"),
    "extraction_incomplete": ("extraction", "medium", "required fields could not be extracted"),
    "check_error": ("system", "medium", "a check failed to run"),
}


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str

    @property
    def source(self) -> str:
        return CODES[self.code][0]

    @property
    def severity(self) -> str:
        return CODES[self.code][1]

    def to_json(self) -> dict:
        return {"code": self.code, "source": self.source, "severity": self.severity, "detail": self.detail}


def _close(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol + 1e-9


# ------------------------------------------------------------------------------------------- arithmetic
def payslip_arithmetic(f: dict) -> list[Finding]:
    out = []
    if not _close(f["basic_salary"] + (f["allowances"] or 0), f["gross_pay"]):
        out.append(Finding("arithmetic_mismatch", f"basic {f['basic_salary']:.2f} + allowances {f['allowances'] or 0:.2f} != gross {f['gross_pay']:.2f}"))
    parts = [f[k] for k in ("epf_employee", "socso_employee", "eis_employee", "pcb")]
    if all(p is not None for p in parts) and not _close(sum(parts), f["total_deductions"]):
        out.append(Finding("arithmetic_mismatch", f"deductions sum {sum(parts):.2f} != total {f['total_deductions']:.2f}"))
    if not _close(f["gross_pay"] - f["total_deductions"], f["net_pay"]):
        out.append(Finding("arithmetic_mismatch", f"gross - deductions {f['gross_pay'] - f['total_deductions']:.2f} != net {f['net_pay']:.2f}"))
    return out


def payslip_statutory(f: dict) -> list[Finding]:
    """Simplified statutory rates (see statutory.py). Real tables have bands and exceptions (voluntary EPF,
    exempt allowances), which is why a mismatch goes to a human rather than to rejection."""
    g, out = f["gross_pay"], []
    expected = {"epf_employee": (statutory.epf(g), 1.0), "socso_employee": (statutory.socso(g), 0.05),
                "eis_employee": (statutory.eis(g), 0.05)}
    for key, (want, tol) in expected.items():
        if f.get(key) is not None and not _close(f[key], want, tol):
            out.append(Finding("statutory_mismatch", f"{key} {f[key]:.2f}, expected about {want:.2f} for gross {g:.2f}"))
    return out


def statement_arithmetic(f: dict) -> list[Finding]:
    out, bal = [], f["opening_balance"]
    for i, row in enumerate(f["transactions"]):
        bal = round(bal + (row["credit"] or 0) - (row["debit"] or 0), 2)
        if not _close(bal, row["balance"]):
            out.append(Finding("arithmetic_mismatch", f"row {i + 1} ({row['date']}): running balance {bal:.2f} != printed {row['balance']:.2f}"))
            break
    credits = round(sum(r["credit"] or 0 for r in f["transactions"]), 2)
    debits = round(sum(r["debit"] or 0 for r in f["transactions"]), 2)
    if f.get("total_credits") is not None and not _close(credits, f["total_credits"]):
        out.append(Finding("arithmetic_mismatch", f"credits sum {credits:.2f} != total credits {f['total_credits']:.2f}"))
    if f.get("total_debits") is not None and not _close(debits, f["total_debits"]):
        out.append(Finding("arithmetic_mismatch", f"debits sum {debits:.2f} != total debits {f['total_debits']:.2f}"))
    if not _close(f["opening_balance"] + credits - debits, f["closing_balance"]):
        out.append(Finding("arithmetic_mismatch", f"opening + credits - debits {f['opening_balance'] + credits - debits:.2f} != closing {f['closing_balance']:.2f}"))
    return out


# ------------------------------------------------------------------------------------------- identity
def identity(doc_type: str, f: dict, app: dict) -> list[Finding]:
    out = []
    ref = date.fromisoformat(app["application_date"])
    shown = f.get("employee_name") if doc_type == "payslip" else f.get("account_holder")
    status, score = names.compare(shown, app["applicant_name"])
    if status == "mismatch":
        out.append(Finding("name_mismatch", f"name similarity {score:.2f}"))
    elif status == "partial":
        out.append(Finding("name_partial_match", f"name similarity {score:.2f}"))
    for who, value in (("application", app.get("nric")), ("document", f.get("nric"))):
        if value and (p := nric.problems(value, ref)):
            out.append(Finding("nric_invalid", f"{who} NRIC: {'; '.join(p)}"))
    if f.get("nric") and app.get("nric") and nric.normalise(f["nric"]) != nric.normalise(app["nric"]):
        out.append(Finding("nric_mismatch", "document NRIC differs from the application"))
    return out


# ------------------------------------------------------------------------------------------- dates
def _period_bounds(period: str) -> tuple[date, date]:
    y, m = (int(x) for x in period.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def dates(doc_type: str, f: dict, app: dict, grace_days: int, max_age_days: int) -> list[Finding]:
    out = []
    ref = date.fromisoformat(app["application_date"])
    if doc_type == "payslip":
        start, end = _period_bounds(f["pay_period"])
        paid = date.fromisoformat(f["pay_date"])
        if not start <= paid <= end + timedelta(days=grace_days):
            out.append(Finding("date_outside_period", f"pay date {paid} is outside period {f['pay_period']} (+{grace_days} days)"))
        latest = max(end, paid)
    else:
        start, end = date.fromisoformat(f["period_start"]), date.fromisoformat(f["period_end"])
        bad = [r["date"] for r in f["transactions"] if r["date"] and not start <= date.fromisoformat(r["date"]) <= end]
        if end < start or bad:
            out.append(Finding("date_outside_period", f"{len(bad)} transaction date(s) outside {start}..{end}"))
        latest = end
    if start > ref or latest > ref + timedelta(days=grace_days):
        out.append(Finding("document_future_dated", f"document period {start}..{end} is after application date {ref}"))
    elif (ref - end).days > max_age_days:
        out.append(Finding("document_stale", f"period ended {(ref - end).days} days before the application (max {max_age_days})"))
    return out


# ------------------------------------------------------------------------------------------- forensics
def _family(software: str | None) -> str:
    m = re.match(r"\W*([A-Za-z0-9]+)", software or "")
    return m.group(1).lower() if m else ""


def metadata(facts: PdfFacts, editor_patterns: list[str], modified_after_days: int) -> list[Finding]:
    out = []
    meta = facts.metadata
    software = " | ".join(s for s in (meta.get("producer"), meta.get("creator")) if s)
    hit = next((p for p in editor_patterns if p.lower() in software.lower()), None)
    if hit:
        out.append(Finding("pdf_editor_producer", f"producer/creator matches editing tool {hit!r}"))
    if facts.revisions > 1:
        out.append(Finding("pdf_incremental_update", f"{facts.revisions} saved revisions"))
    created, modified = meta.get("created"), meta.get("modified")
    if created and modified and (modified - created).total_seconds() > modified_after_days * 86400:
        days = (modified - created).days
        if _family(meta.get("producer")) != _family(meta.get("creator")):
            out.append(Finding("pdf_rewritten_after_creation", f"modified {days} days after creation by different software than the creator"))
        else:
            out.append(Finding("pdf_modified_after_creation", f"modified {days} days after creation by the same software"))
    return out


def fonts(facts: PdfFacts) -> list[Finding]:
    """All amounts should share one font family (bold allowed), and amounts in comparable rows one size.
    Rows that start with a date (statement transactions) are compared with each other; all other amounts
    (payslip lines, statement totals) with each other."""
    amounts = []
    for row in _rows(facts.spans):
        group = "dated_rows" if row and _DATE_SPAN.match(row[0].text) else "other"
        amounts += [(group, s) for s in row if s.is_amount]
    if len(amounts) < 2:
        return []
    out = []
    families = Counter(s.family for _, s in amounts)
    if len(families) > 1:
        out.append(Finding("font_inconsistent", "amount fonts: " + ", ".join(f"{k} x{v}" for k, v in families.most_common())))
    for group in ("dated_rows", "other"):
        sizes = Counter(s.size for g, s in amounts if g == group)
        if len(sizes) > 1:
            out.append(Finding("font_inconsistent", f"amount sizes ({group}): " + ", ".join(f"{k}pt x{v}" for k, v in sizes.most_common())))
    return out
