"""Read a PDF's text layer, fonts and metadata, and parse payslip / bank-statement fields.

This is text-layer extraction (pypdf): it works on digitally generated PDFs, which is how payslips and
e-statements usually arrive. A scanned image has no text layer; such files are reported as unreadable and
go to manual review (OCR is a documented next step, not implemented).

Payslip fields are parsed from text with bilingual (English / Malay) label patterns. Statement rows are
parsed from positioned text spans, because debit and credit columns cannot be told apart in plain text.
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .schema import FIELDS, REQUIRED, parse_money

MONEY_RE = r"([\d,]+\.\d{2})"
_AMOUNT_SPAN = re.compile(r"^[\d,]+\.\d{2}[+-]?$")
_MONTHS = {m.lower(): i + 1 for names in (
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    ["Januari", "Februari", "Mac", "April", "Mei", "Jun", "Julai", "Ogos", "September", "Oktober", "November", "Disember"],
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    ["Jan", "Feb", "Mac", "Apr", "Mei", "Jun", "Jul", "Ogo", "Sep", "Okt", "Nov", "Dis"]) for i, m in enumerate(names)}
DATE_RE = r"(\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2} [A-Za-z]{3,9} \d{4})"


@dataclass
class Span:
    text: str
    font: str      # base font name without subset prefix, e.g. Helvetica-Bold
    size: float
    x: float
    y: float
    page: int

    @property
    def family(self) -> str:
        return re.split(r"[-,]", self.font)[0].replace("Roman", "") or self.font

    @property
    def is_amount(self) -> bool:
        return bool(_AMOUNT_SPAN.match(self.text))


@dataclass
class PdfFacts:
    text: str
    spans: list[Span]
    metadata: dict          # producer, creator, created, modified (datetimes or None)
    revisions: int          # number of saved revisions (incremental updates add one each)
    pages: int
    sha256: str
    error: str | None = None


def parse_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    try:
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        m = re.match(r"^(\d{1,2}) ([A-Za-z]{3,9}) (\d{4})$", s)
        if m and m.group(2).lower() in _MONTHS:
            return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
    except ValueError:
        return None
    return None


def parse_period(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d{1,2})/(\d{4})$", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.match(r"^([A-Za-z]+) (\d{4})$", s)
    if m and m.group(1).lower() in _MONTHS:
        return f"{m.group(2)}-{_MONTHS[m.group(1).lower()]:02d}"
    return None


def _meta_dt(value) -> datetime | None:
    return value if isinstance(value, datetime) else None


def read_pdf(data: bytes) -> PdfFacts:
    from pypdf import PdfReader

    sha = hashlib.sha256(data).hexdigest()
    revisions = max(1, data.count(b"%%EOF"))
    try:
        reader = PdfReader(io.BytesIO(data))
        spans: list[Span] = []
        texts = []
        for pno, page in enumerate(reader.pages):
            def visit(text, cm, tm, font_dict, font_size, _p=pno):
                t = text.strip()
                if t and font_dict is not None:
                    base = str(font_dict.get("/BaseFont", "")).lstrip("/")
                    base = base.split("+", 1)[1] if re.match(r"^[A-Z]{6}\+", base) else base
                    spans.append(Span(t, base, round(float(font_size) * abs(float(tm[0]) or 1.0), 2),
                                      round(float(tm[4]), 1), round(float(tm[5]), 1), _p))
            texts.append(page.extract_text(visitor_text=visit) or "")
        meta = reader.metadata or {}
        info = {"producer": meta.get("/Producer"), "creator": meta.get("/Creator"),
                "created": _meta_dt(getattr(meta, "creation_date", None)),
                "modified": _meta_dt(getattr(meta, "modification_date", None))}
        return PdfFacts("\n".join(texts), spans, info, revisions, len(reader.pages), sha)
    except Exception as e:  # malformed or encrypted file: reported, never raised to the caller
        return PdfFacts("", [], {}, revisions, 0, sha, error=f"{type(e).__name__}")


def detect_type(text: str) -> str | None:
    head = text[:600].upper()
    if re.search(r"STATEMENT OF ACCOUNT|ACCOUNT STATEMENT|PENYATA AKAUN", head):
        return "bank_statement"
    if re.search(r"PAYSLIP|PAY SLIP|SLIP GAJI|PENYATA GAJI", head):
        return "payslip"
    return None


# ------------------------------------------------------------------------------------------- payslip
_PAYSLIP_MONEY = {
    "basic_salary": r"(?:Basic Salary|Gaji Pokok)",
    "gross_pay": r"(?:Gross Pay|Jumlah Kasar|Jumlah Pendapatan|Total Earnings)",
    "epf_employee": r"(?:EPF \(Employee\)|KWSP / EPF\b|KWSP(?! Majikan| /))",
    "socso_employee": r"(?:PERKESO|SOCSO)\b",
    "eis_employee": r"(?:\bSIP|\bEIS)\b",
    "pcb": r"(?:\bPCB|\bMTD)\b",
    "total_deductions": r"(?:Total Deductions|Jumlah Potongan)",
    "net_pay": r"(?:Net Pay|Net Salary|Gaji Bersih)",
}
_ALLOWANCE = re.compile(r"(?:\bElaun [A-Z][a-z]+(?: / [A-Z][a-z]+ Allowance)?|\b[A-Z][a-z]+ Allowance)[^\d\n]*?" + MONEY_RE)
_STOP = r"(?=\s+(?:No\. K/P|NRIC|IC No|No\. Pekerja|Bulan|Tarikh)|\n|$)"


def _first(pattern: str, text: str, flags=0) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def parse_payslip(text: str) -> dict:
    out: dict = {k: None for k in FIELDS["payslip"]}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out["employer"] = lines[0] if lines else None
    out["employee_name"] = _first(r"(?:Employee Name|Nama / Name|Nama|Name)\s*:\s*(.+?)" + _STOP, text, re.M)
    out["nric"] = _first(r"(?:NRIC|No\. K/P(?: / IC No)?|IC No)\.?\s*:\s*(\d{6}-?\d{2}-?\d{4})", text)
    out["employee_no"] = _first(r"(?:Employee No|No\. Pekerja(?: / Staff ID)?|Staff ID)\s*:\s*([A-Z0-9-]+)", text)
    out["pay_period"] = parse_period(_first(r"(?:Pay Period|Bulan(?: / Month)?|Month)\s*:\s*(.+?)" + _STOP, text, re.M))
    out["pay_date"] = parse_date(_first(r"(?:Pay Date|Tarikh Bayaran(?: / Pay Date)?)\s*:\s*" + DATE_RE, text))
    for key, label in _PAYSLIP_MONEY.items():
        v = _first(label + r"[^\d\n]*?" + MONEY_RE, text)
        out[key] = parse_money(v) if v else None
    out["allowances"] = round(sum(parse_money(m.group(1)) for m in _ALLOWANCE.finditer(text)), 2)
    return out


# ------------------------------------------------------------------------------------------- statement
_STATEMENT_MONEY = {
    "opening_balance": r"(?:Opening Balance|Balance B/F|Baki Pembukaan)",
    "total_credits": r"(?:Total Credits?|Jumlah Kredit)",
    "total_debits": r"(?:Total Debits?|Jumlah Debit)",
    "closing_balance": r"(?:Closing Balance|Balance C/F|Baki Penutup)",
}
_DATE_SPAN = re.compile(r"^" + DATE_RE + r"$")


def _rows(spans: list[Span], tol: float = 3.0) -> list[list[Span]]:
    """Group spans into visual rows. Baselines within `tol` points are one row: text in a different font
    size sits slightly higher or lower, and an edited figure must not split its row."""
    rows: list[list[Span]] = []
    for s in sorted(spans, key=lambda s: (s.page, -s.y)):
        if rows and rows[-1][0].page == s.page and abs(rows[-1][0].y - s.y) <= tol:
            rows[-1].append(s)
        else:
            rows.append([s])
    return [sorted(r, key=lambda s: s.x) for r in rows]


def parse_transactions(spans: list[Span]) -> list[dict] | None:
    columns: dict[str, float] = {}
    txns = []
    for row in _rows(spans):
        words = {s.text.lower(): s.x for s in row}
        if ("debit" in words and ("credit" in words or "kredit" in words)):
            columns = {"debit": words["debit"], "credit": words.get("credit", words.get("kredit"))}
            continue
        if not row or not _DATE_SPAN.match(row[0].text):
            continue
        amounts = [s for s in row[1:] if s.is_amount]
        desc = " ".join(s.text for s in row[1:] if not s.is_amount)
        if len(amounts) != 2:
            return None                       # unexpected row shape: let a human look
        amt, bal = amounts
        value = parse_money(amt.text)
        if amt.text.endswith("+"):
            side = "credit"
        elif amt.text.endswith("-"):
            side = "debit"
        elif columns:
            side = "credit" if amt.x >= columns["credit"] - 1 else "debit"
        else:
            return None
        txns.append({"date": parse_date(row[0].text), "description": desc, "debit": value if side == "debit" else None,
                     "credit": value if side == "credit" else None, "balance": parse_money(bal.text)})
    return txns


def parse_statement(text: str, spans: list[Span]) -> dict:
    out: dict = {k: None for k in FIELDS["bank_statement"]}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out["bank"] = lines[0] if lines else None
    out["account_holder"] = _first(r"(?:Account Holder|Account Name|Nama Pemegang Akaun)\s*:\s*(.+)", text)
    out["account_no"] = _first(r"(?:Account No|Account Number|No\. Akaun)\.?\s*:\s*([\d-]+)", text)
    m = re.search(r"(?:Statement Period|Period|Tempoh Penyata)\s*:\s*" + DATE_RE + r"\s*(?:-|to|hingga)\s*" + DATE_RE, text)
    if m:
        out["period_start"], out["period_end"] = parse_date(m.group(1)), parse_date(m.group(2))
    for key, label in _STATEMENT_MONEY.items():
        v = _first(label + r"\s*:?\s*" + MONEY_RE, text)
        out[key] = parse_money(v) if v else None
    out["transactions"] = parse_transactions(spans)
    return out


@dataclass
class Extraction:
    doc_type: str | None
    fields: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    method: str = "text_layer"


def extract(facts: PdfFacts) -> Extraction:
    doc_type = detect_type(facts.text)
    if doc_type is None:
        return Extraction(None)
    fields = parse_payslip(facts.text) if doc_type == "payslip" else parse_statement(facts.text, facts.spans)
    missing = [k for k in REQUIRED[doc_type] if fields.get(k) in (None, [])]
    return Extraction(doc_type, fields, missing)
