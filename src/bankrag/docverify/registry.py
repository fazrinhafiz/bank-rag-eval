"""Duplicate-submission registry.

Two keys per document: the file hash (an exact copy) and a content fingerprint built from fields a forger
rarely changes when re-using someone else's document (employer + staff number + period + amounts for a
payslip; bank + account number + period + balances for a statement). The fingerprint still matches when
only the name and NRIC were edited.

Applicants are compared by a salted hash of their NRIC (or name tokens), never the raw value, so the
persistent registry holds no personal data. `SubmissionRegistry` keeps it in memory (evaluation, tests);
`SqliteRegistry` keeps it in the audit database so it survives restarts and is shared between workers.
"""
from __future__ import annotations

import hashlib
import json

from ..tracing import query_hash
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


def identity_hash(app: dict) -> str:
    ident = nric.normalise(app.get("nric")) or " ".join(sorted(names.tokens(app.get("applicant_name"))))
    return query_hash("identity:" + ident)


def registry_keys(doc_type: str | None, fields: dict, sha256: str) -> list[str]:
    keys = [f"sha:{sha256}"]
    fp = fingerprint(doc_type, fields) if doc_type else None
    return keys + [f"fp:{fp}"] if fp else keys


class SubmissionRegistry:
    """In-memory store. Subclasses override _prior and _add."""

    def __init__(self):
        self._seen: dict[str, list[tuple[str, str]]] = {}
        self.last_keys: list[str] = []

    def _prior(self, keys: list[str]) -> list[tuple[str, str]]:
        return [p for k in keys for p in self._seen.get(k, [])]

    def _add(self, keys: list[str], application_id: str, ident: str) -> None:
        for k in keys:
            if (application_id, ident) not in self._seen.setdefault(k, []):
                self._seen[k].append((application_id, ident))

    def check_and_add(self, app: dict, doc_type: str | None, fields: dict, sha256: str) -> list[Finding]:
        keys = registry_keys(doc_type, fields, sha256)
        self.last_keys = keys
        me = identity_hash(app)
        prior = [(a, i) for a, i in self._prior(keys) if a != app["application_id"]]
        others = sorted({a for a, i in prior if i != me})
        out = []
        if others:
            out.append(Finding("duplicate_other_applicant", f"matches {len(others)} earlier application(s) by another applicant"))
        elif prior:
            out.append(Finding("resubmitted_same_applicant", "seen before from the same applicant"))
        self._add(keys, app["application_id"], me)
        return out


class SqliteRegistry(SubmissionRegistry):
    """Same logic, stored in the audit database's document_registry table."""

    def __init__(self, audit):
        super().__init__()
        self.audit = audit

    def check_and_add(self, app: dict, doc_type: str | None, fields: dict, sha256: str) -> list[Finding]:
        with self.audit._lock:           # check-then-add is atomic within the process
            return super().check_and_add(app, doc_type, fields, sha256)

    def _prior(self, keys: list[str]) -> list[tuple[str, str]]:
        marks = ",".join("?" * len(keys))
        _, rows = self.audit.query(f"SELECT application_id, identity_hash FROM document_registry WHERE key IN ({marks})", tuple(keys))
        return rows

    def _add(self, keys: list[str], application_id: str, ident: str) -> None:
        self.audit.conn.executemany("INSERT OR IGNORE INTO document_registry VALUES (?, ?, ?)",
                                    [(k, application_id, ident) for k in keys])
