"""Duplicate-submission registry.

Two keys per document: the file hash (an exact copy) and a content fingerprint built from fields a forger
rarely changes when re-using someone else's document (employer + staff number + period + amounts for a
payslip; bank + account number + period + balances for a statement). The fingerprint still matches when
only the name and NRIC were edited. In memory here; M8 persists it in the audit database.
"""
from __future__ import annotations

import hashlib
import json

from . import names, nric
from .checks import Finding

FINGERPRINT_FIELDS = {
    "payslip": ("employer", "employee_no", "pay_period", "gross_pay", "net_pay"),
    "bank_statement": ("bank", "account_no", "period_end", "opening_balance", "closing_balance"),
}


def fingerprint(doc_type: str, fields: dict) -> str | None:
    values = [fields.get(k) for k in FINGERPRINT_FIELDS[doc_type]]
    if any(v is None for v in values):
        return None
    norm = [" ".join(v.upper().split()) if isinstance(v, str) else v for v in values]
    return hashlib.sha256(json.dumps([doc_type, *norm]).encode()).hexdigest()[:24]


class SubmissionRegistry:
    def __init__(self):
        self._seen: dict[str, list[dict]] = {}

    def _identity(self, app: dict) -> str:
        return nric.normalise(app.get("nric")) or " ".join(sorted(names.tokens(app.get("applicant_name"))))

    def check_and_add(self, app: dict, doc_type: str | None, fields: dict, sha256: str) -> list[Finding]:
        keys = [f"sha:{sha256}"]
        fp = fingerprint(doc_type, fields) if doc_type else None
        if fp:
            keys.append(f"fp:{fp}")
        me = self._identity(app)
        out = []
        prior = [p for k in keys for p in self._seen.get(k, []) if p["application_id"] != app["application_id"]]
        others = sorted({p["application_id"] for p in prior if p["identity"] != me})
        if others:
            out.append(Finding("duplicate_other_applicant", f"matches {len(others)} earlier application(s) by another applicant"))
        elif prior:
            out.append(Finding("resubmitted_same_applicant", "seen before from the same applicant"))
        for k in keys:
            self._seen.setdefault(k, []).append({"application_id": app["application_id"], "identity": me})
        return out
