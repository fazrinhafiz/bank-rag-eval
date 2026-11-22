"""Verify one submitted document against its application.

    read PDF -> extract fields -> consistency checks -> forensics -> anomaly score -> duplicate registry
             -> rule engine -> Verdict (decision + every reason + every finding)

Each check runs in isolation: if one raises, a `check_error` finding is recorded and the document goes to
manual review instead of being accepted on the strength of the checks that did run (fail closed).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date

from ..config import resolve
from . import checks, nric
from .anomaly import AnomalyModel
from .checks import Finding
from .extract import Extraction, extract, read_pdf
from .registry import SubmissionRegistry
from .rules import RuleSet

log = logging.getLogger("bankrag.docverify")
CHECK_GROUPS = ("arithmetic", "statutory", "identity", "dates", "metadata", "fonts", "anomaly", "registry")


@dataclass
class Verdict:
    decision: str
    reasons: list[dict]
    findings: list[Finding]
    doc_type: str | None
    fields: dict
    missing: list[str]
    anomaly_score: float | None
    anomaly_threshold: float | None
    sha256: str
    rules_version: str
    extraction_method: str
    latency_ms: float = 0.0
    checks_run: list[str] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        return sorted({f.code for f in self.findings})

    def to_json(self) -> dict:
        """For callers and reviewers. NRIC masked to its last 4 digits; transactions summarised."""
        shown = dict(self.fields)
        if "nric" in shown:
            shown["nric"] = nric.mask(shown["nric"])
        if isinstance(shown.get("transactions"), list):
            shown["transactions"] = f"{len(shown['transactions'])} rows"
        return {"decision": self.decision, "reasons": self.reasons, "findings": [f.to_json() for f in self.findings],
                "doc_type": self.doc_type, "fields": shown, "missing_fields": self.missing,
                "anomaly_score": self.anomaly_score, "anomaly_threshold": self.anomaly_threshold,
                "sha256": self.sha256, "rules_version": self.rules_version,
                "extraction_method": self.extraction_method, "checks_run": self.checks_run}


def validate_application(app: dict) -> list[str]:
    errors = [f"missing {k}" for k in ("application_id", "applicant_name", "application_date") if not app.get(k)]
    try:
        date.fromisoformat(app.get("application_date") or "")
    except ValueError:
        errors.append("application_date must be YYYY-MM-DD")
    return errors


class DocumentVerifier:
    def __init__(self, cfg: dict, rules: RuleSet | None = None, registry: SubmissionRegistry | None = None,
                 anomaly: AnomalyModel | None | bool = True, extractor=None, disabled: set[str] = frozenset()):
        dv = cfg["docverify"]
        self.rules = rules or RuleSet.load(resolve(cfg, dv["rules"]))
        self.settings = self.rules.settings
        self.registry = registry if registry is not None else SubmissionRegistry()
        if anomaly is True:
            anomaly = AnomalyModel.load(resolve(cfg, dv["anomaly_model"])) if self.settings.get("anomaly", True) else None
        self.anomaly = anomaly or None
        self.extractor = extractor            # optional: callable(PdfFacts) -> Extraction | None (e.g. LLM)
        self.disabled = set(disabled)
        unknown = self.disabled - set(CHECK_GROUPS)
        if unknown:
            raise ValueError(f"unknown check group(s): {sorted(unknown)}")

    def _extract(self, facts) -> Extraction:
        if self.extractor is None:
            return extract(facts)
        result = self.extractor(facts)
        return result if result is not None else Extraction(extract(facts).doc_type, {}, ["llm_extraction_failed"], "llm")

    def verify(self, pdf: bytes, application: dict) -> Verdict:
        start = time.perf_counter()
        errors = validate_application(application)
        if errors:
            raise ValueError("; ".join(errors))
        s = self.settings
        findings: list[Finding] = []
        ran: list[str] = []
        score = thr = None

        def run(group: str, fn, *args):
            if group in self.disabled:
                return
            ran.append(group)
            try:
                findings.extend(fn(*args))
            except Exception as e:  # a broken check must not let a document through
                log.warning("check failed", extra={"event": "check_error", "check": group, "error_type": type(e).__name__})
                findings.append(Finding("check_error", f"{group}: {type(e).__name__}"))

        facts = read_pdf(pdf)
        ex = Extraction(None, {}, [], "none")
        if facts.error:
            findings.append(Finding("unreadable_pdf", facts.error))
        else:
            ex = self._extract(facts)
            if ex.doc_type is None:
                findings.append(Finding("unsupported_document", "no payslip or bank statement header in the text layer"))
            elif ex.missing:
                findings.append(Finding("extraction_incomplete", "missing: " + ", ".join(ex.missing)))
            else:
                f, t = ex.fields, ex.doc_type
                run("arithmetic", checks.payslip_arithmetic if t == "payslip" else checks.statement_arithmetic, f)
                if t == "payslip":
                    run("statutory", checks.payslip_statutory, f)
                run("identity", checks.identity, t, f, application)
                run("dates", checks.dates, t, f, application, s.get("pay_date_grace_days", 10), s.get("max_document_age_days", 90))
                if self.anomaly is not None and "anomaly" not in self.disabled:
                    ran.append("anomaly")
                    score, thr, why = self.anomaly.score(t, f)
                    if score is not None and score > thr:
                        findings.append(Finding("anomaly_score_high", f"score {score:.3f} > threshold {thr:.3f}" + (f"; {why}" if why else "")))
            run("metadata", checks.metadata, facts, s.get("editor_patterns", []), s.get("modified_after_days", 2))
            run("fonts", checks.fonts, facts)
        run("registry", self.registry.check_and_add, application, ex.doc_type, ex.fields, facts.sha256)

        decision = self.rules.decide(findings)
        verdict = Verdict(decision.decision, decision.reasons, findings, ex.doc_type, ex.fields, ex.missing, score, thr,
                          facts.sha256, self.rules.version, ex.method, round((time.perf_counter() - start) * 1000, 2), ran)
        # Never log field values: they are personal data. Codes and the decision are enough to investigate.
        log.info("document decision", extra={"event": "doc_decision", "application_id": application["application_id"],
                                             "doc_type": verdict.doc_type, "decision": verdict.decision,
                                             "codes": verdict.codes, "rules_version": verdict.rules_version,
                                             "latency_ms": verdict.latency_ms})
        return verdict
