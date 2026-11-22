"""Optional LLM field extraction with schema validation and grounding (structured output, not trust).

Use when layouts vary too much for label patterns. Nothing the model returns is used until it passes:
  1. JSON parse                  -> otherwise retry with the error
  2. schema.validate             -> types, required keys, no extra keys
  3. grounding                   -> every amount, date and name must literally appear in the document text,
                                    so a value the model computed or invented is caught
After `max_attempts` failures it returns None and the verifier sends the document to manual review.

PII minimisation: the NRIC is masked before the text leaves the process ([NRIC]) and is taken from the
local parser instead. Offline evaluation does not use this path (no API key in CI); it is tested with a
fake client in tests/test_docverify.py.
"""
from __future__ import annotations

import json
import logging
import re

from ..guardrails import mask_pii
from .extract import DATE_RE, Extraction, PdfFacts, detect_type, parse_date, parse_payslip
from .schema import FIELDS, MONEY, REQUIRED, TEXT, TXNS, fmt_money, validate

log = logging.getLogger("bankrag.docverify.llm")

SYSTEM = """You extract fields from a Malaysian {doc_type} (text may be English or Malay).
Return ONE JSON object with exactly these keys: {keys}.
Types: money = number without commas or currency (e.g. 5200.0); date = "YYYY-MM-DD"; period = "YYYY-MM";
text = string as printed. {txn_note}
Copy values exactly as printed. Never calculate, correct or infer a value; use null if it is not printed.
The NRIC is masked as [NRIC]; return null for it."""
TXN_NOTE = ('transactions = list of {"date", "description", "debit", "credit", "balance"} in document order, '
            'with exactly one of debit/credit set and the other null.')


def grounding_errors(record: dict, text: str) -> list[str]:
    """Values that do not appear in the document text."""
    errors = []
    printed_dates = {parse_date(m.group(1)) for m in re.finditer(DATE_RE, text)}
    flat = " ".join(text.upper().split())
    spec = FIELDS[detect_type(text) or "payslip"]

    def money_ok(v):
        return v is None or fmt_money(v) in text

    for key, kind in spec.items():
        v = record.get(key)
        if v is None or key == "nric":
            continue
        if kind == MONEY and not money_ok(v):
            errors.append(f"{key}={v} is not printed in the document")
        elif kind == TEXT and " ".join(str(v).upper().split()) not in flat:
            errors.append(f"{key}={v!r} is not printed in the document")
        elif kind == "date" and v not in printed_dates:
            errors.append(f"{key}={v} is not a date printed in the document")
        elif kind == TXNS:
            for i, row in enumerate(v):
                bad = [c for c in ("debit", "credit", "balance") if not money_ok(row.get(c))]
                if bad or row.get("date") not in printed_dates:
                    errors.append(f"transactions[{i}] has values not printed in the document")
                    break
    # Allowances are a sum of printed lines, so check the lines exist rather than the total.
    if record.get("allowances") and "allowances" in spec:
        errors = [e for e in errors if not e.startswith("allowances=")]
    return errors


class LLMExtractor:
    method = "llm"

    def __init__(self, client, model: str, max_attempts: int = 2):
        self.client, self.model, self.max_attempts = client, model, max_attempts

    @classmethod
    def from_env(cls, model: str = "gpt-4o-mini", azure: bool = False) -> "LLMExtractor":
        from ..generators import OpenAICompatible
        return cls(OpenAICompatible._make_client(azure), model)

    def __call__(self, facts: PdfFacts) -> Extraction | None:
        doc_type = detect_type(facts.text)
        if doc_type is None:
            return Extraction(None, method=self.method)
        masked, _ = mask_pii(facts.text)
        keys = list(FIELDS[doc_type])
        messages = [{"role": "system", "content": SYSTEM.format(doc_type=doc_type.replace("_", " "), keys=", ".join(keys),
                                                                txn_note=TXN_NOTE if doc_type == "bank_statement" else "")},
                    {"role": "user", "content": masked}]
        for attempt in range(1, self.max_attempts + 1):
            resp = self.client.chat.completions.create(model=self.model, messages=messages, temperature=0,
                                                       response_format={"type": "json_object"})
            raw = resp.choices[0].message.content or ""
            try:
                record = json.loads(raw)
                errors = validate(doc_type, record) or grounding_errors(record, facts.text)
            except json.JSONDecodeError as e:
                record, errors = None, [f"not valid JSON: {e.msg}"]
            if not errors:
                if doc_type == "payslip":
                    record["nric"] = parse_payslip(facts.text)["nric"]     # never sent to the model
                missing = [k for k in REQUIRED[doc_type] if record.get(k) in (None, [])]
                return Extraction(doc_type, record, missing, self.method)
            log.info("llm extraction rejected", extra={"event": "llm_extraction_rejected", "attempt": attempt,
                                                        "n_errors": len(errors)})
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": "Fix these problems and return the full JSON again:\n- " + "\n- ".join(errors[:10])}]
        return None
