"""Field schema for the two supported document types, and a validator for extracted records.

The same schema is used for the rule-based parser and for the optional LLM extractor, whose JSON output is
validated against it before anything downstream trusts it.
"""
from __future__ import annotations

import re
from datetime import date

MONEY, TEXT, DATE, PERIOD, TXNS = "money", "text", "date", "period", "transactions"

FIELDS: dict[str, dict[str, str]] = {
    "payslip": {
        "employer": TEXT, "employee_name": TEXT, "nric": TEXT, "employee_no": TEXT,
        "pay_period": PERIOD, "pay_date": DATE,
        "basic_salary": MONEY, "allowances": MONEY, "gross_pay": MONEY,
        "epf_employee": MONEY, "socso_employee": MONEY, "eis_employee": MONEY, "pcb": MONEY,
        "total_deductions": MONEY, "net_pay": MONEY,
    },
    "bank_statement": {
        "bank": TEXT, "account_holder": TEXT, "account_no": TEXT,
        "period_start": DATE, "period_end": DATE,
        "opening_balance": MONEY, "total_credits": MONEY, "total_debits": MONEY, "closing_balance": MONEY,
        "transactions": TXNS,
    },
}
# Without these a document cannot be checked, so it goes to a human.
REQUIRED = {
    "payslip": ["employee_name", "pay_period", "pay_date", "basic_salary", "gross_pay", "total_deductions", "net_pay"],
    "bank_statement": ["account_holder", "period_start", "period_end", "opening_balance", "closing_balance", "transactions"],
}
_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TXN_KEYS = {"date", "description", "debit", "credit", "balance"}


def _is_money(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 and abs(round(v, 2) - v) < 1e-6


def _is_date(v) -> bool:
    try:
        date.fromisoformat(v)
        return isinstance(v, str)
    except (TypeError, ValueError):
        return False


def validate(doc_type: str, record: dict) -> list[str]:
    """Errors (empty = valid). Values may be null when the field is absent from the document."""
    if doc_type not in FIELDS:
        return [f"unknown doc_type {doc_type!r}"]
    if not isinstance(record, dict):
        return ["record is not an object"]
    spec, errors = FIELDS[doc_type], []
    errors += [f"unknown field {k!r}" for k in record if k not in spec]
    for name, kind in spec.items():
        if name not in record:
            errors.append(f"missing key {name!r}")
            continue
        v = record[name]
        if v is None:
            continue
        ok = {MONEY: _is_money, TEXT: lambda x: isinstance(x, str) and x.strip() != "",
              DATE: _is_date, PERIOD: lambda x: isinstance(x, str) and bool(_PERIOD.match(x))}.get(kind)
        if kind == TXNS:
            if not isinstance(v, list):
                errors.append(f"{name}: expected a list")
                continue
            for i, row in enumerate(v):
                if not isinstance(row, dict) or set(row) != TXN_KEYS:
                    errors.append(f"{name}[{i}]: expected keys {sorted(TXN_KEYS)}")
                elif not (_is_date(row["date"]) and _is_money(row["balance"])
                          and all(row[k] is None or _is_money(row[k]) for k in ("debit", "credit"))
                          and (row["debit"] is None) != (row["credit"] is None)):
                    errors.append(f"{name}[{i}]: bad values (one of debit/credit, ISO date, money)")
        elif not ok(v):
            errors.append(f"{name}: expected {kind}, got {v!r}")
    return errors


def parse_money(s: str) -> float:
    return round(float(s.replace(",", "").replace("RM", "").strip().rstrip("+-")), 2)


def fmt_money(v: float) -> str:
    return f"{v:,.2f}"
