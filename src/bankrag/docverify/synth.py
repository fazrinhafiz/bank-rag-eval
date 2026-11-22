"""Synthetic Malaysian payslips and bank statements, genuine and tampered, with ground truth.

Everything here is fictional: people, NRICs, employers, banks and account numbers are random. Payroll uses
SIMPLIFIED statutory rules (EPF 11% rounded up to the ringgit, SOCSO 0.5% and EIS 0.2% of wages capped at
RM6,000, a simplified progressive PCB with a per-person relief factor) - close enough to give realistic
structure, not the official contribution tables.

Genuine documents carry the variation that makes real intake hard: name spellings (MOHD, BT., upper case,
word order, dropped particles, occasionally a shortened name), payslips "printed to PDF" by the employee,
and bank statements re-downloaded later (modification date long after creation). Each tampered document
carries ONE kind of tampering so per-kind recall can be measured; real forgeries often leave several traces
at once, which makes them easier, not harder, to catch.

Layouts: payslip P1 (English), P2 (bilingual, Courier amounts), P3 (Malay, two columns); statement S1
(signed amounts), S2 (debit/credit columns), S3 (Malay). P3 and S3 appear in the TEST split only, so the
test split measures how extraction and checks cope with a layout they were not developed against.
"""
from __future__ import annotations

import copy
import hashlib
import io
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import nric as nric_mod
from .schema import fmt_money
from .statutory import eis, epf, epf_employer, pcb, socso

TAMPER_KINDS = ("arithmetic", "consistent_forgery", "font_inconsistency", "metadata_modified",
                "invalid_nric", "period_mismatch", "name_mismatch", "duplicate_resubmission")
TAMPER_DESCRIPTIONS = {
    "arithmetic": "one figure inflated, totals left unchanged",
    "consistent_forgery": "salary inflated and every total recomputed; tax (PCB) left as it was",
    "font_inconsistency": "consistent forgery, edited figures drawn in a different font or size",
    "metadata_modified": "consistent forgery, saved again by an editing tool (incremental update or full rewrite)",
    "invalid_nric": "fabricated identity: impossible NRIC (payslips only; statements carry no NRIC)",
    "period_mismatch": "old document relabelled with a recent period; dates inside it not changed",
    "name_mismatch": "someone else's genuine document submitted",
    "duplicate_resubmission": "a document already submitted by another applicant, as-is or with the name and NRIC changed",
}
TEMPLATES = {"dev": {"payslip": ["P1", "P2"], "bank_statement": ["S1", "S2"]},
             "test": {"payslip": ["P1", "P2", "P3"], "bank_statement": ["S1", "S2", "S3"]}}

MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]
MONTHS_MS = ["Januari", "Februari", "Mac", "April", "Mei", "Jun", "Julai", "Ogos", "September", "Oktober",
             "November", "Disember"]
MON_EN = [m[:3] for m in MONTHS_EN]
MON_MS = ["Jan", "Feb", "Mac", "Apr", "Mei", "Jun", "Jul", "Ogo", "Sep", "Okt", "Nov", "Dis"]

MALAY_M = ["Muhammad Hafiz", "Ahmad Faris", "Muhammad Amirul", "Muhammad Irfan", "Khairul Anuar", "Syafiq Aiman",
           "Azlan Shah", "Hakim Danial", "Muhammad Firdaus", "Amir Hamzah"]
MALAY_F = ["Nurul Aina", "Siti Aisyah", "Nur Izzati", "Farah Hanim", "Aminah Zahra", "Nur Azura", "Syazwani Huda",
           "Liyana Sofea", "Siti Khadijah", "Nur Batrisyia"]
MALAY_FATHERS = ["Ahmad", "Rosli", "Ismail", "Abdullah", "Hassan", "Zainal", "Kamarudin", "Yusof", "Osman", "Razak",
                 "Muhammad Salleh", "Abdul Rahman"]
CHINESE_SURNAMES = ["Tan", "Lim", "Lee", "Wong", "Ng", "Chan", "Ong", "Teh", "Goh", "Chong", "Yap", "Low"]
CHINESE_GIVEN = ["Wei Ming", "Mei Ling", "Jia Hui", "Kok Leong", "Siew Lan", "Chee Keong", "Hui Min", "Yong Sheng",
                 "Pei Shan", "Zhi Hao"]
INDIAN_M = ["Arjun", "Suresh", "Ravi Kumar", "Ganesh", "Vikram", "Prakash", "Dinesh"]
INDIAN_F = ["Priya", "Kavitha", "Deepa", "Lakshmi", "Anitha", "Shalini", "Revathi"]
INDIAN_FATHERS = ["Raman", "Muthu", "Krishnan", "Subramaniam", "Rajendran", "Selvam", "Arumugam"]

EMPLOYERS = [  # name, short name, address, producer, creator
    ("Syarikat Maju Jaya Sdn Bhd", "MAJU JAYA", "Lot 12, Jalan Perusahaan 3, 40150 Shah Alam", "PayrollOne 4.2", "PayrollOne"),
    ("Teras Kencana Logistik Sdn Bhd", "TERAS KENCANA", "No. 8, Jalan Pelabuhan, 42000 Port Klang", "GajiPro 7.1", "GajiPro"),
    ("Lembah Hijau Agro Bhd", "LEMBAH HIJAU", "KM 14, Jalan Kuantan-Gambang, 26300 Kuantan", "HRKita Cloud", "HRKita"),
    ("Nusantara Byte Solutions Sdn Bhd", "NUSANTARA BYTE", "Level 9, Menara Cempaka, 50450 Kuala Lumpur", "PayrollOne 4.2", "PayrollOne"),
    ("Permata Cahaya Retail Sdn Bhd", "PERMATA CAHAYA", "No. 21, Jalan Tun Razak, 80000 Johor Bahru", "GajiPro 7.1", "GajiPro"),
    ("Seri Kenari Healthcare Sdn Bhd", "SERI KENARI", "No. 5, Lebuh Pantai, 10300 George Town", "HRKita Cloud", "HRKita"),
    ("Delta Rimba Engineering Sdn Bhd", "DELTA RIMBA", "No. 77, Jalan Tun Abdul Razak, 93000 Kuching", "KiraGaji Desktop 2", "KiraGaji"),
]
BANKS = [  # name, producer, creator, account-number format
    ("Bank Teratai Berhad", "Teratai eStatement 3.1", "Teratai eStatement", "{0}{1}{2}{3}-{4}{5}{6}{7}-{8}{9}{10}{11}"),
    ("Bank Delima Utara Berhad", "BDU iStatement 2.4", "BDU iStatement", "{0}{1}{2}{3}{4}{5}{6}{7}{8}{9}{10}{11}"),
    ("Bank Seri Andalas Berhad", "Andalas DocGen 5", "Andalas DocGen", "{0}{1}-{2}{3}{4}{5}{6}{7}-{8}{9}{10}{11}"),
]
ALLOWANCES = [("Transport", "Pengangkutan"), ("Phone", "Telefon"), ("Meal", "Makan"), ("Housing", "Perumahan"), ("Shift", "Syif")]
DEBITS = [("BILL PAYMENT TNB", 60, 450), ("BILL PAYMENT UNIFI", 129, 199), ("DUITNOW TRF TO {who}", 50, 1500),
          ("CARD PAYMENT", 200, 2500), ("ATM WITHDRAWAL", 100, 800), ("POS PURCHASE GROCER", 20, 350),
          ("E-WALLET RELOAD", 20, 300), ("INSURANCE PREMIUM", 80, 400), ("HOUSING LOAN INSTALMENT", 800, 2800),
          ("CAR LOAN INSTALMENT", 400, 1500)]
EDITORS = ["PDFescape Online Editor", "Sejda PDF Editor", "iLovePDF", "Smallpdf PDF Editor", "Foxit PDF Editor 12"]
REWRITERS = ["GPL Ghostscript 10.03.1", "PDF-XChange Core API SDK"]
PRINT_TO_PDF = ("Microsoft: Print To PDF", "Microsoft: Print To PDF")
VALID_PLACE = [*range(1, 15)]
INVALID_PLACE = [0, 17, 18, 19, 20, 69, 70, 73, 80, 81, 94, 95, 96, 97]


# --------------------------------------------------------------------------------------------- people
@dataclass
class Person:
    name: str            # canonical, as declared on the application
    nric: str
    male: bool
    group: str           # malay | chinese | indian
    given: str
    family: str


def make_person(rng: random.Random) -> Person:
    group = rng.choices(["malay", "chinese", "indian"], [0.6, 0.27, 0.13])[0]
    male = rng.random() < 0.5
    if group == "malay":
        given = rng.choice(MALAY_M if male else MALAY_F)
        family = rng.choice(MALAY_FATHERS)
        name = f"{given} {'bin' if male else 'binti'} {family}"
    elif group == "chinese":
        given, family = rng.choice(CHINESE_GIVEN), rng.choice(CHINESE_SURNAMES)
        name = f"{family} {given}"
    else:
        given = rng.choice(INDIAN_M if male else INDIAN_F)
        family = rng.choice(INDIAN_FATHERS)
        name = f"{given} {'a/l' if male else 'a/p'} {family}"
    dob = date(1968, 1, 1) + timedelta(days=rng.randint(0, 365 * 35))
    last = rng.randrange(1 if male else 0, 10, 2)
    nric = f"{dob:%y%m%d}-{rng.choice(VALID_PLACE):02d}-{rng.randint(0, 999):03d}{last}"
    return Person(name, nric, male, group, given, family)


def display_name(rng: random.Random, p: Person) -> str:
    """How the name is printed on a genuine document."""
    r = rng.random()
    if r < 0.40:
        return p.name
    if r < 0.70:
        return p.name.upper()
    if r < 0.83 and p.group == "malay":
        out = p.name.replace("Muhammad", "Mohd").replace(" binti ", " Bt. ").replace(" bin ", " B. ")
        return out.upper() if rng.random() < 0.5 else out
    if r < 0.88 and p.group == "chinese":
        return f"{p.given} {p.family}".upper()
    if r < 0.95 and p.group != "chinese":
        return f"{p.given} {p.family}".upper()          # particle dropped
    if p.group != "chinese":
        return p.given.upper()                         # shortened name: a legitimate partial match
    return p.name.upper()


def fabricated_nric(rng: random.Random, p: Person) -> str:
    yy, mm, dd = p.nric[:2], int(p.nric[2:4]), int(p.nric[4:6])
    how = rng.choice(["month", "day", "place"])
    if how == "month":
        return f"{yy}{rng.randint(13, 19):02d}{dd:02d}{p.nric[6:]}"
    if how == "day":
        return f"{yy}02{rng.choice([30, 31]):02d}{p.nric[6:]}" if rng.random() < 0.5 else f"{yy}{mm:02d}{rng.randint(32, 39)}{p.nric[6:]}"
    return f"{p.nric[:7]}{rng.choice(INVALID_PLACE):02d}{p.nric[9:]}"


# --------------------------------------------------------------------------------------------- payroll
def payroll(basic: float, allowances: float, relief_factor: float, pcb_amt: float | None = None) -> dict:
    gross = round(basic + allowances, 2)
    e, s, i = epf(gross), socso(gross), eis(gross)
    p = pcb(gross, e, relief_factor) if pcb_amt is None else pcb_amt
    total = round(e + s + i + p, 2)
    return {"basic_salary": basic, "allowances": round(allowances, 2), "gross_pay": gross, "epf_employee": e,
            "socso_employee": s, "eis_employee": i, "pcb": p, "total_deductions": total,
            "net_pay": round(gross - total, 2)}


# --------------------------------------------------------------------------------------------- specs
@dataclass
class DocSpec:
    doc_type: str
    template: str
    fields: dict                  # exactly what is printed: the extraction ground truth
    extras: dict                  # layout-only data (allowance lines, address, employer EPF)
    created: datetime
    producer: str
    creator: str
    font_overrides: dict = field(default_factory=dict)   # field key -> (family, style, size)
    post: tuple | None = None     # ("rewrite"|"incremental", producer, modified datetime)


def _ts(d: date, rng: random.Random, hour: int = 9) -> datetime:
    return datetime(d.year, d.month, d.day, hour, rng.randint(0, 59), rng.randint(0, 59), tzinfo=timezone.utc)


def _month_add(year: int, month: int, delta: int) -> tuple[int, int]:
    m = year * 12 + month - 1 + delta
    return m // 12, m % 12 + 1


def _month_end(y: int, m: int) -> date:
    ny, nm = _month_add(y, m, 1)
    return date(ny, nm, 1) - timedelta(days=1)


def payslip_spec(rng: random.Random, person: Person, shown_name: str, employer: tuple, template: str,
                 year: int, month: int, employee_no: str, salary: dict) -> DocSpec:
    name, short, address, producer, creator = employer
    pay_date = date(year, month, salary["pay_day"])
    items = salary["allowance_items"]
    pay = payroll(salary["basic"], sum(a for *_, a in items), salary["relief"])
    fields = {"employer": name, "employee_name": shown_name, "nric": person.nric, "employee_no": employee_no,
              "pay_period": f"{year}-{month:02d}", "pay_date": pay_date.isoformat(), **pay}
    extras = {"address": address, "allowance_items": items, "epf_employer": epf_employer(pay["gross_pay"]),
              "employer_short": short}
    return DocSpec("payslip", template, fields, extras, _ts(pay_date, rng), producer, creator)


def _transactions(rng: random.Random, start: date, end: date, opening: float, salary_day: date | None,
                  net_pay: float, employer_short: str) -> list[dict]:
    days = (end - start).days + 1
    events = []
    if salary_day is not None:
        events.append((salary_day, 0, f"SALARY {employer_short}", None, net_pay))
    for k in range(rng.randint(6, 16)):
        desc, lo, hi = rng.choice(DEBITS)
        amount = round(rng.uniform(lo, hi), 2) if "UNIFI" not in desc else float(rng.choice([129, 159, 199]))
        if "ATM" in desc:
            amount = float(round(amount / 50) * 50)
        who = rng.choice(MALAY_FATHERS + CHINESE_SURNAMES + INDIAN_FATHERS).upper()
        events.append((start + timedelta(days=rng.randrange(days)), 1 + k, desc.format(who=who), amount, None))
    for k in range(rng.randint(0, 3)):
        who = rng.choice(MALAY_FATHERS + CHINESE_SURNAMES).upper()
        events.append((start + timedelta(days=rng.randrange(days)), 50 + k, f"DUITNOW TRF FR {who}", None,
                       round(rng.uniform(20, 800), 2)))
    events.append((end, 99, "PROFIT PAID", None, round(opening * rng.uniform(0.0003, 0.0012) + 0.05, 2)))
    rows, bal = [], opening
    for d, _, desc, debit, credit in sorted(events, key=lambda e: (e[0], e[1])):
        if debit is not None and bal - debit < 0:
            continue
        bal = round(bal + (credit or 0) - (debit or 0), 2)
        rows.append({"date": d.isoformat(), "description": desc, "debit": debit, "credit": credit, "balance": bal})
    return rows


def recompute_statement(fields: dict) -> None:
    bal = fields["opening_balance"]
    for row in fields["transactions"]:
        bal = round(bal + (row["credit"] or 0) - (row["debit"] or 0), 2)
        row["balance"] = bal
    fields["closing_balance"] = bal
    fields["total_credits"] = round(sum(r["credit"] or 0 for r in fields["transactions"]), 2)
    fields["total_debits"] = round(sum(r["debit"] or 0 for r in fields["transactions"]), 2)


def statement_spec(rng: random.Random, shown_name: str, bank: tuple, template: str, account_no: str,
                   year: int, month: int, salary_day: date | None, net_pay: float, employer_short: str) -> DocSpec:
    bank_name, producer, creator, _ = bank
    start, end = date(year, month, 1), _month_end(year, month)
    opening = round(rng.uniform(300, 30000), 2)
    rows = _transactions(rng, start, end, opening, salary_day, net_pay, employer_short)
    fields = {"bank": bank_name, "account_holder": shown_name, "account_no": account_no,
              "period_start": start.isoformat(), "period_end": end.isoformat(), "opening_balance": opening,
              "total_credits": 0.0, "total_debits": 0.0, "closing_balance": 0.0, "transactions": rows}
    recompute_statement(fields)
    return DocSpec("bank_statement", template, fields, {}, _ts(end + timedelta(days=1), rng, hour=2), producer, creator)


# --------------------------------------------------------------------------------------------- rendering
def _period_text(template: str, period: str) -> str:
    y, m = (int(x) for x in period.split("-"))
    return {"P1": f"{MONTHS_EN[m - 1]} {y}", "P2": f"{m:02d}/{y}", "P3": f"{MONTHS_MS[m - 1]} {y}"}[template]


def _date_text(template: str, iso: str) -> str:
    d = date.fromisoformat(iso)
    return {"P1": f"{d:%d/%m/%Y}", "P2": f"{d:%d-%m-%Y}", "P3": f"{d.day:02d} {MON_MS[d.month - 1]} {d.year}",
            "S1": f"{d:%d/%m/%Y}", "S2": f"{d.day:02d} {MON_EN[d.month - 1]} {d.year}", "S3": f"{d:%d/%m/%Y}"}[template]


class _Writer:
    """Thin wrapper so every amount goes through one place that applies font overrides (the tamper)."""

    def __init__(self, spec: DocSpec, label_font: str, amount_font: str):
        from fpdf import FPDF

        self.pdf = FPDF(format="A4")
        self.pdf.set_auto_page_break(True, margin=12)
        self.pdf.set_margins(10, 10, 10)
        self.pdf.set_creation_date(spec.created)
        self.pdf.set_producer(spec.producer)
        self.pdf.set_creator(spec.creator)
        self.pdf.add_page()
        self.spec, self.lf, self.af = spec, label_font, amount_font

    def text(self, w, s, style="", size=10, ln=False, h=6, align="L"):
        self.pdf.set_font(self.lf, style, size)
        self.pdf.cell(w, h, s, align=align, new_x="LMARGIN" if ln else "RIGHT", new_y="NEXT" if ln else "TOP")

    def amount(self, w, key, value, style="", size=10, ln=False, h=6, suffix=""):
        family, st, sz = self.spec.font_overrides.get(key, (self.af, style, size))
        self.pdf.set_font(family, st, sz)
        self.pdf.cell(w, h, fmt_money(value) + suffix, align="R", new_x="LMARGIN" if ln else "RIGHT",
                      new_y="NEXT" if ln else "TOP")

    def bytes(self) -> bytes:
        return bytes(self.pdf.output())


def _render_payslip(spec: DocSpec) -> bytes:
    f, x, t = spec.fields, spec.extras, spec.template
    items = x["allowance_items"]
    if t == "P1":
        w = _Writer(spec, "helvetica", "helvetica")
        w.text(0, f["employer"], "B", 14, ln=True, h=8)
        w.text(0, x["address"], size=9, ln=True, h=5)
        w.pdf.ln(3)
        w.text(0, "PAYSLIP", "B", 12, ln=True, h=7)
        for label, value in (("Name", f["employee_name"]), ("NRIC", f["nric"]), ("Employee No", f["employee_no"]),
                             ("Pay Period", _period_text(t, f["pay_period"])), ("Pay Date", _date_text(t, f["pay_date"]))):
            w.text(35, f"{label}:")
            w.text(0, value, ln=True)
        w.pdf.ln(2)
        w.text(0, "EARNINGS", "B", ln=True)
        rows = [("Basic Salary", "basic_salary", f["basic_salary"], "")]
        rows += [(f"{en} Allowance", f"allowance:{en}", a, "") for en, _, a in items]
        rows += [("Gross Pay", "gross_pay", f["gross_pay"], "B")]
        for label, key, value, st in rows:
            w.text(90, label, st)
            w.amount(35, key, value, st, ln=True)
        w.pdf.ln(2)
        w.text(0, "DEDUCTIONS", "B", ln=True)
        for label, key, st in (("EPF (Employee)", "epf_employee", ""), ("SOCSO", "socso_employee", ""), ("EIS", "eis_employee", ""),
                               ("PCB (Income Tax)", "pcb", ""), ("Total Deductions", "total_deductions", "B"), ("Net Pay", "net_pay", "B")):
            w.text(90, label, st)
            w.amount(35, key, f[key], st, ln=True)
        w.pdf.ln(2)
        w.text(0, "EMPLOYER CONTRIBUTIONS", "B", ln=True)
        w.text(90, "EPF (Employer)")
        w.amount(35, "epf_employer", x["epf_employer"], ln=True)
    elif t == "P2":
        w = _Writer(spec, "helvetica", "courier")
        w.text(0, f["employer"].upper(), "B", 13, ln=True, h=8)
        w.text(0, x["address"], size=9, ln=True, h=5)
        w.pdf.ln(3)
        w.text(0, "SLIP GAJI / PAYSLIP", "B", 12, ln=True, h=7)
        for label, value in (("Nama / Name", f["employee_name"]), ("No. K/P / IC No", f["nric"]),
                             ("No. Pekerja / Staff ID", f["employee_no"]), ("Bulan / Month", _period_text(t, f["pay_period"])),
                             ("Tarikh Bayaran / Pay Date", _date_text(t, f["pay_date"]))):
            w.text(55, f"{label}:")
            w.text(0, value, ln=True)
        w.pdf.ln(2)
        rows = [("Gaji Pokok / Basic Salary", "basic_salary", f["basic_salary"], "")]
        rows += [(f"Elaun {ms} / {en} Allowance", f"allowance:{en}", a, "") for en, ms, a in items]
        rows += [("Jumlah Kasar / Gross Pay", "gross_pay", f["gross_pay"], "B"),
                 ("KWSP / EPF", "epf_employee", f["epf_employee"], ""), ("PERKESO / SOCSO", "socso_employee", f["socso_employee"], ""),
                 ("SIP / EIS", "eis_employee", f["eis_employee"], ""), ("PCB / MTD", "pcb", f["pcb"], ""),
                 ("Jumlah Potongan / Total Deductions", "total_deductions", f["total_deductions"], "B"),
                 ("Gaji Bersih / Net Pay", "net_pay", f["net_pay"], "B"),
                 ("Caruman Majikan KWSP / Employer EPF", "epf_employer", x["epf_employer"], "")]
        for label, key, value, st in rows:
            w.text(100, label, st)
            w.amount(40, key, value, st, ln=True)
    else:  # P3: Malay, earnings and deductions side by side
        w = _Writer(spec, "times", "times")
        w.text(0, f["employer"], "B", 14, ln=True, h=8)
        w.text(0, x["address"], size=9, ln=True, h=5)
        w.pdf.ln(3)
        w.text(0, "PENYATA GAJI", "B", 12, ln=True, h=7)
        w.text(95, f"Nama: {f['employee_name']}")
        w.text(0, f"No. K/P: {f['nric']}", ln=True)
        w.text(60, f"No. Pekerja: {f['employee_no']}")
        w.text(60, f"Bulan: {_period_text(t, f['pay_period'])}")
        w.text(0, f"Tarikh Bayaran: {_date_text(t, f['pay_date'])}", ln=True)
        w.pdf.ln(2)
        w.text(95, "PENDAPATAN", "B")
        w.text(0, "POTONGAN", "B", ln=True)
        left = [("Gaji Pokok", "basic_salary", f["basic_salary"])] + [(f"Elaun {ms}", f"allowance:{en}", a) for en, ms, a in items]
        right = [("KWSP", "epf_employee", f["epf_employee"]), ("PERKESO", "socso_employee", f["socso_employee"]),
                 ("SIP", "eis_employee", f["eis_employee"]), ("PCB", "pcb", f["pcb"])]
        for i in range(max(len(left), len(right))):
            if i < len(left):
                w.text(60, left[i][0])
                w.amount(30, left[i][1], left[i][2])
                w.text(5, "")
            else:
                w.text(95, "")
            if i < len(right):
                w.text(55, right[i][0])
                w.amount(30, right[i][1], right[i][2])
            w.pdf.ln(6)
        w.text(60, "Jumlah Pendapatan", "B")
        w.amount(30, "gross_pay", f["gross_pay"], "B")
        w.text(5, "")
        w.text(55, "Jumlah Potongan", "B")
        w.amount(30, "total_deductions", f["total_deductions"], "B", ln=True)
        w.pdf.ln(2)
        w.text(60, "Gaji Bersih", "B")
        w.amount(30, "net_pay", f["net_pay"], "B", ln=True)
        w.text(60, "KWSP Majikan")
        w.amount(30, "epf_employer", x["epf_employer"], ln=True)
    w.pdf.ln(4)
    w.text(0, "This is a computer-generated document. No signature is required.", "I" if w.lf != "courier" else "", 8, ln=True)
    return w.bytes()


def _render_statement(spec: DocSpec) -> bytes:
    f, t = spec.fields, spec.template
    labels = {
        "S1": ("STATEMENT OF ACCOUNT", "Account Holder", "Account No", "Statement Period", "-", "Opening Balance",
               "Total Credits", "Total Debits", "Closing Balance"),
        "S2": ("ACCOUNT STATEMENT", "Account Name", "Account Number", "Period", "to", "Balance B/F",
               "Total Credit", "Total Debit", "Balance C/F"),
        "S3": ("PENYATA AKAUN", "Nama Pemegang Akaun", "No. Akaun", "Tempoh Penyata", "hingga", "Baki Pembukaan",
               "Jumlah Kredit", "Jumlah Debit", "Baki Penutup"),
    }[t]
    title, holder, acct, period, sep, opening, tcr, tdr, closing = labels
    w = _Writer(spec, "times" if t == "S3" else "helvetica", "times" if t == "S3" else "helvetica")
    w.text(0, f["bank"].upper(), "B", 14, ln=True, h=8)
    w.text(0, title, "B", 12, ln=True, h=7)
    w.text(50, f"{holder}:")
    w.text(0, f["account_holder"], ln=True)
    w.text(50, f"{acct}:")
    w.text(0, f["account_no"], ln=True)
    w.text(50, f"{period}:")
    w.text(0, f"{_date_text(t, f['period_start'])} {sep} {_date_text(t, f['period_end'])}", ln=True)
    w.text(50, f"{opening}:")
    w.amount(40, "opening_balance", f["opening_balance"], ln=True)
    w.pdf.ln(3)
    if t == "S1":
        cols = [("Date", 25), ("Description", 85), ("Amount", 40), ("Balance", 40)]
    else:
        heads = ("Date", "Description", "Debit", "Credit", "Balance") if t == "S2" else ("Tarikh", "Butiran", "Debit", "Kredit", "Baki")
        cols = list(zip(heads, (25, 75, 30, 30, 30)))
    for name, width in cols:
        w.text(width, name, "B", 9)
    w.pdf.ln(6)
    for i, row in enumerate(f["transactions"]):
        w.text(25, _date_text(t, row["date"]), size=9)
        if t == "S1":
            w.text(85, row["description"], size=9)
            if row["credit"] is not None:
                w.amount(40, ("txn", i, "credit"), row["credit"], size=9, suffix="+")
            else:
                w.amount(40, ("txn", i, "debit"), row["debit"], size=9, suffix="-")
            w.amount(40, ("txn", i, "balance"), row["balance"], size=9, ln=True)
        else:
            w.text(75, row["description"], size=9)
            for col in ("debit", "credit"):
                if row[col] is None:
                    w.text(30, "", size=9)
                else:
                    w.amount(30, ("txn", i, col), row[col], size=9)
            w.amount(30, ("txn", i, "balance"), row["balance"], size=9, ln=True)
    w.pdf.ln(3)
    for label, key in ((tcr, "total_credits"), (tdr, "total_debits"), (closing, "closing_balance")):
        w.text(50, f"{label}:", "B")
        w.amount(40, key, f[key], "B", ln=True)
    return w.bytes()


def _pdf_date(dt: datetime) -> str:
    return dt.strftime("D:%Y%m%d%H%M%S+00'00'")


def render(spec: DocSpec) -> bytes:
    data = _render_payslip(spec) if spec.doc_type == "payslip" else _render_statement(spec)
    if spec.post is None:
        return data
    from pypdf import PdfReader, PdfWriter

    how, producer, modified = spec.post
    meta = {"/Producer": producer, "/Creator": spec.creator, "/CreationDate": _pdf_date(spec.created),
            "/ModDate": _pdf_date(modified)}
    if how == "incremental":
        writer = PdfWriter(io.BytesIO(data), incremental=True)
    else:
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.add_metadata(meta)
    # A fixed file id keeps the corpus byte-for-byte reproducible.
    digest = hashlib.md5(data + producer.encode()).hexdigest()
    from pypdf.generic import ArrayObject, ByteStringObject
    writer._ID = ArrayObject([ByteStringObject(bytes.fromhex(digest)), ByteStringObject(bytes.fromhex(digest))])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# --------------------------------------------------------------------------------------------- tampering
FONT_SWAP = {"helvetica": "times", "courier": "helvetica", "times": "helvetica"}


def _amount_font(spec: DocSpec) -> str:
    return {"P1": "helvetica", "P2": "courier", "P3": "times", "S1": "helvetica", "S2": "helvetica", "S3": "times"}[spec.template]


def inflate_payslip(rng: random.Random, spec: DocSpec, recompute: bool) -> list:
    """Raise basic salary. recompute=True keeps every total consistent except PCB. Returns changed keys."""
    f = spec.fields
    new_basic = float(round(f["basic_salary"] * rng.uniform(1.5, 2.5) / 50) * 50)
    if not recompute:
        f["basic_salary"] = new_basic
        return ["basic_salary"]
    before = dict(f)
    f.update(payroll(new_basic, f["allowances"], 1.0, pcb_amt=f["pcb"]))
    spec.extras["epf_employer"] = epf_employer(f["gross_pay"])
    return [k for k in f if f[k] != before[k]] + ["epf_employer"]


def inflate_statement(rng: random.Random, spec: DocSpec, recompute: bool) -> list:
    f = spec.fields
    idx = next((i for i, r in enumerate(f["transactions"]) if r["description"].startswith("SALARY")), None)
    if idx is None:
        idx = max(range(len(f["transactions"])), key=lambda i: f["transactions"][i]["credit"] or 0)
    row = f["transactions"][idx]
    row["credit"] = float(round((row["credit"] or 100) * rng.uniform(1.5, 2.5) / 10) * 10)
    if not recompute:
        return [("txn", idx, "credit")]
    recompute_statement(f)
    return [("txn", idx, "credit")] + [("txn", i, "balance") for i in range(idx, len(f["transactions"]))] + \
        ["closing_balance", "total_credits"]


# --------------------------------------------------------------------------------------------- corpus
@dataclass
class Submission:
    id: str
    split: str
    doc_type: str
    label: str            # genuine | one of TAMPER_KINDS
    variant: str
    template: str
    application: dict     # application_id, applicant_name, nric, application_date
    pdf: bytes
    truth: dict           # printed field values

    @property
    def tampered(self) -> bool:
        return self.label != "genuine"

    def manifest(self) -> dict:
        return {"id": self.id, "doc_type": self.doc_type, "label": self.label, "variant": self.variant,
                "template": self.template, "sha256": hashlib.sha256(self.pdf).hexdigest()[:16]}


class _Factory:
    def __init__(self, split: str, seed: int):
        self.split, self.rng = split, random.Random(seed)
        self.used_ids: set[str] = set()
        self.n_app = 0

    def unique(self, fmt: callable) -> str:
        while True:
            v = fmt()
            if v not in self.used_ids:
                self.used_ids.add(v)
                return v

    def application(self, person: Person, app_date: date, nric: str | None = None) -> dict:
        self.n_app += 1
        return {"application_id": f"APP-{self.split}-{self.n_app:04d}", "applicant_name": person.name,
                "nric": nric or person.nric, "application_date": app_date.isoformat()}

    def app_date(self) -> date:
        return date(2026, 10, 5) + timedelta(days=self.rng.randint(0, 46))

    def salary(self) -> dict:
        r = self.rng
        basic = float(round(math.exp(r.uniform(math.log(2200), math.log(15000))) / 50) * 50)
        items = [(en, ms, float(round(r.uniform(50, 800), -1))) for en, ms in r.sample(ALLOWANCES, r.randint(0, 3))]
        return {"basic": basic, "allowance_items": items, "relief": r.uniform(0.75, 1.0), "pay_day": r.randint(25, 28)}

    def payslip(self, person: Person, template: str, app_date: date, months_back: int = 1, shown: str | None = None) -> DocSpec:
        y, m = _month_add(app_date.year, app_date.month, -months_back)
        employer = self.rng.choice(EMPLOYERS)
        emp_no = self.unique(lambda: f"E{self.rng.randint(10000, 99999)}")
        return payslip_spec(self.rng, person, shown or display_name(self.rng, person), employer, template, y, m, emp_no, self.salary())

    def statement(self, person: Person, template: str, app_date: date, months_back: int = 1, shown: str | None = None) -> DocSpec:
        y, m = _month_add(app_date.year, app_date.month, -months_back)
        bank = self.rng.choice(BANKS)
        acct = self.unique(lambda: bank[3].format(*[self.rng.randint(0, 9) for _ in range(12)]))
        sal = self.salary()
        net = payroll(sal["basic"], sum(a for *_, a in sal["allowance_items"]), sal["relief"])["net_pay"]
        employer_short = self.rng.choice(EMPLOYERS)[1]
        return statement_spec(self.rng, shown or display_name(self.rng, person), bank, template, acct, y, m,
                              date(y, m, sal["pay_day"]), net, employer_short)

    def doc(self, doc_type: str, *a, **kw) -> DocSpec:
        return (self.payslip if doc_type == "payslip" else self.statement)(*a, **kw)


def _genuine_noise(rng: random.Random, spec: DocSpec, app_date: date) -> str:
    """Legitimate variations in how genuine files reach the bank."""
    if spec.doc_type == "payslip" and rng.random() < 0.10:
        spec.producer, spec.creator = PRINT_TO_PDF
        spec.created = _ts(app_date - timedelta(days=rng.randint(1, 5)), rng, hour=20)
        return "printed_to_pdf"
    if spec.doc_type == "bank_statement" and rng.random() < 0.20:
        spec.post = ("rewrite", spec.producer, _ts(app_date - timedelta(days=rng.randint(1, 10)), rng, hour=21))
        return "redownloaded"
    return "plain"


def build_corpus(split: str, seed: int, n_genuine: int = 60, n_per_kind: int = 10) -> list[Submission]:
    """Genuine submissions first, then tampered ones, so the duplicate registry sees originals first."""
    fac, rng = _Factory(split, seed), None
    rng = fac.rng
    out: list[Submission] = []
    genuine_pool: dict[str, list[tuple[Person, DocSpec, Submission]]] = {"payslip": [], "bank_statement": []}

    def add(doc_type, label, variant, spec, app):
        out.append(Submission(f"{split}-{len(out):04d}", split, doc_type, label, variant, spec.template, app,
                              render(spec), copy.deepcopy(spec.fields)))
        return out[-1]

    for doc_type in ("payslip", "bank_statement"):
        templates = TEMPLATES[split][doc_type]
        for i in range(n_genuine):
            person, app_date = make_person(rng), fac.app_date()
            spec = fac.doc(doc_type, person, templates[i % len(templates)], app_date, months_back=rng.choice([1, 1, 1, 2]))
            variant = _genuine_noise(rng, spec, app_date)
            sub = add(doc_type, "genuine", variant, spec, fac.application(person, app_date))
            genuine_pool[doc_type].append((person, spec, sub))

    for doc_type in ("payslip", "bank_statement"):
        templates = TEMPLATES[split][doc_type]
        for kind in TAMPER_KINDS:
            if kind == "invalid_nric" and doc_type == "bank_statement":
                continue
            for i in range(n_per_kind):
                template = templates[i % len(templates)]
                person, app_date = make_person(rng), fac.app_date()
                app = None
                variant = ""
                if kind in ("arithmetic", "consistent_forgery", "font_inconsistency", "metadata_modified"):
                    spec = fac.doc(doc_type, person, template, app_date)
                    inflate = inflate_payslip if doc_type == "payslip" else inflate_statement
                    if kind == "arithmetic":
                        if doc_type == "payslip" and i % 2:
                            spec.fields["net_pay"] = float(round(spec.fields["net_pay"] * rng.uniform(1.3, 2.0), -1))
                            variant = "net_pay_only"
                        elif doc_type == "bank_statement" and i % 2:
                            spec.fields["closing_balance"] = float(round(spec.fields["closing_balance"] * rng.uniform(1.5, 3.0) + 1000, -1))
                            variant = "closing_only"
                        else:
                            inflate(rng, spec, recompute=False)
                            variant = "basic_only" if doc_type == "payslip" else "credit_only"
                    else:
                        changed = inflate(rng, spec, recompute=True)
                        variant = "recomputed"
                        if kind == "font_inconsistency":
                            base = _amount_font(spec)
                            size = 10 if doc_type == "payslip" else 9
                            if i % 2:
                                over, variant = (base, "", size - 1), "size"
                            else:
                                over, variant = (FONT_SWAP[base], "", size), "family"
                            spec.font_overrides = {k: over for k in changed}
                        elif kind == "metadata_modified":
                            modified = spec.created + timedelta(days=rng.randint(2, 30), hours=rng.randint(0, 12))
                            if i % 2:
                                spec.post, variant = ("rewrite", rng.choice(REWRITERS), modified), "full_rewrite"
                            else:
                                spec.post, variant = ("incremental", rng.choice(EDITORS), modified), "editor_incremental"
                elif kind == "invalid_nric":
                    fake = fabricated_nric(rng, person)
                    spec = fac.doc(doc_type, person, template, app_date)
                    spec.fields["nric"] = fake
                    app = fac.application(person, app_date, nric=fake)
                    variant = "fabricated"
                elif kind == "period_mismatch":
                    spec = fac.doc(doc_type, person, template, app_date, months_back=rng.randint(4, 7))
                    y, m = _month_add(app_date.year, app_date.month, -1)
                    if doc_type == "payslip":
                        spec.fields["pay_period"] = f"{y}-{m:02d}"
                    else:
                        spec.fields["period_start"] = date(y, m, 1).isoformat()
                        spec.fields["period_end"] = _month_end(y, m).isoformat()
                    variant = "relabelled"
                elif kind == "name_mismatch":
                    other = make_person(rng)
                    spec = fac.doc(doc_type, other, template, app_date)
                    variant = "other_person"
                else:  # duplicate_resubmission
                    orig_person, orig_spec, orig_sub = genuine_pool[doc_type][rng.randrange(len(genuine_pool[doc_type]))]
                    app_date = date.fromisoformat(orig_sub.application["application_date"]) + timedelta(days=rng.randint(1, 20))
                    if i % 2:
                        out.append(Submission(f"{split}-{len(out):04d}", split, doc_type, kind, "exact_copy", orig_sub.template,
                                              fac.application(person, app_date), orig_sub.pdf, copy.deepcopy(orig_sub.truth)))
                        continue
                    spec = copy.deepcopy(orig_spec)
                    key = "employee_name" if doc_type == "payslip" else "account_holder"
                    spec.fields[key] = display_name(rng, person)
                    if doc_type == "payslip":
                        spec.fields["nric"] = person.nric
                    variant = "identity_swapped"
                add(doc_type, kind, variant, spec, app or fac.application(person, app_date))
    return out


def corpus_hash(subs: list[Submission]) -> str:
    h = hashlib.sha256()
    for s in subs:
        h.update(s.pdf)
    return h.hexdigest()[:12]
