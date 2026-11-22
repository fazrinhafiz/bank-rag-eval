"""Document intake: extraction, checks, forensics, registry, rule engine, verifier, LLM extractor."""
import copy
import io
import json
import logging
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("pypdf")
pytest.importorskip("fpdf")

from bankrag.config import load_config  # noqa: E402
from bankrag.docverify import checks, names, nric  # noqa: E402
from bankrag.docverify.anomaly import AnomalyModel  # noqa: E402
from bankrag.docverify.checks import CODES, Finding  # noqa: E402
from bankrag.docverify.extract import extract, read_pdf  # noqa: E402
from bankrag.docverify.llm_extract import LLMExtractor  # noqa: E402
from bankrag.docverify.registry import SubmissionRegistry  # noqa: E402
from bankrag.docverify.rules import RuleError, RuleSet  # noqa: E402
from bankrag.docverify.schema import FIELDS, validate  # noqa: E402
from bankrag.docverify.synth import DocSpec, build_corpus, corpus_hash, render  # noqa: E402
from bankrag.docverify.verify import DocumentVerifier  # noqa: E402


def _norm(v):
    return " ".join(v.upper().split()) if isinstance(v, str) else v


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def small():
    """A small test-split corpus: 6 genuine per type (all 3 layouts each) + 1 per tamper kind."""
    return build_corpus("test", 23, n_genuine=6, n_per_kind=1)


def _first(subs, doc_type, label="genuine", template=None):
    return next(s for s in subs if s.doc_type == doc_type and s.label == label and (template is None or s.template == template))


# ------------------------------------------------------------------------------------------- identity helpers
def test_nric_rules():
    ref = date(2026, 11, 1)
    assert nric.problems("900101-14-5678", ref) == []
    assert any("impossible birth date" in p for p in nric.problems("901301-14-5678", ref))   # month 13
    assert any("impossible birth date" in p for p in nric.problems("900230-14-5678", ref))   # 30 Feb
    assert any("place-of-birth" in p for p in nric.problems("900101-17-5678", ref))
    assert any("age" in p for p in nric.problems("150101-14-5678", ref))                     # 11 years old
    assert nric.mask("900101-14-5678") == "XXXXXX-XX-5678"


@pytest.mark.parametrize("a,b,expected", [
    ("Muhammad Hafiz bin Ahmad", "MOHD HAFIZ B. AHMAD", "match"),
    ("Nurul Aina binti Rosli", "NURUL AINA ROSLI", "match"),
    ("Tan Wei Ming", "WEI MING TAN", "match"),
    ("Arjun a/l Raman", "ARJUN A/L RAMAN", "match"),
    ("Nurul Aina binti Rosli", "NURUL AINA", "partial"),
    ("Nurul Aina binti Rosli", "Siti Aisyah binti Hassan", "mismatch"),
])
def test_name_matching(a, b, expected):
    assert names.compare(a, b)[0] == expected


# ------------------------------------------------------------------------------------------- synth + extraction
def test_corpus_is_deterministic_and_test_only_layouts_are_held_out():
    assert corpus_hash(build_corpus("dev", 11, 6, 1)) == corpus_hash(build_corpus("dev", 11, 6, 1))
    dev_layouts = {s.template for s in build_corpus("dev", 11, 6, 1)}
    assert "P3" not in dev_layouts and "S3" not in dev_layouts


@pytest.mark.parametrize("doc_type,template", [("payslip", "P1"), ("payslip", "P2"), ("payslip", "P3"),
                                               ("bank_statement", "S1"), ("bank_statement", "S2"), ("bank_statement", "S3")])
def test_extraction_matches_ground_truth_on_every_layout(small, doc_type, template):
    sub = _first(small, doc_type, template=template)
    ex = extract(read_pdf(sub.pdf))
    assert ex.doc_type == doc_type and ex.missing == []
    assert {k: _norm(v) for k, v in ex.fields.items()} == {k: _norm(v) for k, v in sub.truth.items()}
    assert validate(doc_type, ex.fields) == []


def test_schema_validation_catches_bad_records(small):
    good = copy.deepcopy(_first(small, "payslip").truth)
    assert validate("payslip", good) == []
    assert any("net_pay" in e for e in validate("payslip", {**good, "net_pay": "4,000.00"}))
    assert any("unknown field" in e for e in validate("payslip", {**good, "bonus": 1.0}))
    st = copy.deepcopy(_first(small, "bank_statement").truth)
    st["transactions"][0]["credit"] = 5.0        # both debit and credit set
    if st["transactions"][0]["debit"] is None:
        st["transactions"][0]["debit"] = 5.0
    assert any("transactions[0]" in e for e in validate("bank_statement", st))


# ------------------------------------------------------------------------------------------- consistency checks
def test_arithmetic_checks(small):
    p = copy.deepcopy(_first(small, "payslip").truth)
    assert checks.payslip_arithmetic(p) == []
    p["net_pay"] += 100
    assert [f.code for f in checks.payslip_arithmetic(p)] == ["arithmetic_mismatch"]
    s = copy.deepcopy(_first(small, "bank_statement").truth)
    assert checks.statement_arithmetic(s) == []
    s["transactions"][2]["balance"] += 50
    assert "arithmetic_mismatch" in {f.code for f in checks.statement_arithmetic(s)}


def test_statutory_check(small):
    p = copy.deepcopy(_first(small, "payslip").truth)
    assert checks.payslip_statutory(p) == []
    p["epf_employee"] += 40
    assert [f.code for f in checks.payslip_statutory(p)] == ["statutory_mismatch"]


def test_date_checks(small):
    sub = _first(small, "payslip")
    f, app = copy.deepcopy(sub.truth), sub.application
    assert checks.dates("payslip", f, app, 10, 90) == []
    f["pay_date"] = "2025-01-28"
    assert "date_outside_period" in {x.code for x in checks.dates("payslip", f, app, 10, 90)}
    old = {**app, "application_date": (date.fromisoformat(app["application_date"]) + timedelta(days=200)).isoformat()}
    assert "document_stale" in {x.code for x in checks.dates("payslip", sub.truth, old, 10, 90)}


def test_identity_checks(small):
    sub = _first(small, "payslip")
    assert checks.identity("payslip", sub.truth, sub.application) == [] or \
        {f.code for f in checks.identity("payslip", sub.truth, sub.application)} == {"name_partial_match"}
    other = {**sub.application, "applicant_name": "Siti Khadijah binti Osman", "nric": "880808-08-8888"}
    codes = {f.code for f in checks.identity("payslip", sub.truth, other)}
    assert {"name_mismatch", "nric_mismatch"} <= codes


# ------------------------------------------------------------------------------------------- forensics
def _respec(sub_spec_from, **changes):
    spec = copy.deepcopy(sub_spec_from)
    for k, v in changes.items():
        setattr(spec, k, v)
    return render(spec)


@pytest.fixture(scope="module")
def payslip_spec():
    from bankrag.docverify.synth import EMPLOYERS, Person, payslip_spec as make
    import random
    person = Person("Muhammad Hafiz bin Ahmad", "900101-14-5677", True, "malay", "Muhammad Hafiz", "Ahmad")
    salary = {"basic": 5200.0, "allowance_items": [("Transport", "Pengangkutan", 300.0)], "relief": 0.9, "pay_day": 27}
    return make(random.Random(1), person, person.name, EMPLOYERS[0], "P1", 2026, 10, "E12345", salary)


def test_metadata_forensics(payslip_spec):
    created = payslip_spec.created
    later = created + timedelta(days=9)
    clean = read_pdf(render(payslip_spec))
    assert checks.metadata(clean, ["pdfescape"], 2) == []
    edited = read_pdf(_respec(payslip_spec, post=("incremental", "PDFescape Online Editor", later)))
    assert {f.code for f in checks.metadata(edited, ["pdfescape"], 2)} == \
        {"pdf_editor_producer", "pdf_incremental_update", "pdf_rewritten_after_creation"}
    rewritten = read_pdf(_respec(payslip_spec, post=("rewrite", "GPL Ghostscript 10.03.1", later)))
    assert {f.code for f in checks.metadata(rewritten, ["pdfescape"], 2)} == {"pdf_rewritten_after_creation"}
    redownloaded = read_pdf(_respec(payslip_spec, post=("rewrite", payslip_spec.producer, later)))
    assert [f.code for f in checks.metadata(redownloaded, ["pdfescape"], 2)] == ["pdf_modified_after_creation"]
    assert CODES["pdf_modified_after_creation"][1] == "info"


def test_font_forensics(payslip_spec, small):
    assert checks.fonts(read_pdf(render(payslip_spec))) == []
    # Genuine statements mix bold totals and smaller transaction rows: not an inconsistency.
    assert checks.fonts(read_pdf(_first(small, "bank_statement", template="S1").pdf)) == []
    family = read_pdf(_respec(payslip_spec, font_overrides={"net_pay": ("times", "", 10)}))
    size = read_pdf(_respec(payslip_spec, font_overrides={"net_pay": ("helvetica", "", 9)}))
    assert [f.code for f in checks.fonts(family)] == ["font_inconsistent"]
    assert [f.code for f in checks.fonts(size)] == ["font_inconsistent"]


# ------------------------------------------------------------------------------------------- registry
def test_registry_catches_copies_and_identity_swaps(small):
    sub = _first(small, "payslip")
    reg = SubmissionRegistry()
    assert reg.check_and_add(sub.application, "payslip", sub.truth, "sha-a") == []
    again = {**sub.application, "application_id": "APP-2"}
    assert [f.code for f in reg.check_and_add(again, "payslip", sub.truth, "sha-a")] == ["resubmitted_same_applicant"]
    thief = {"application_id": "APP-3", "applicant_name": "Siti Aisyah binti Hassan", "nric": "910202-10-1234",
             "application_date": sub.application["application_date"]}
    swapped = {**sub.truth, "employee_name": "SITI AISYAH BINTI HASSAN", "nric": "910202-10-1234"}
    assert [f.code for f in reg.check_and_add(thief, "payslip", swapped, "sha-b")] == ["duplicate_other_applicant"]


# ------------------------------------------------------------------------------------------- rule engine
def test_rule_engine_validation_and_precedence(cfg):
    rules = RuleSet.load("rules/document_rules.yaml")
    d = rules.decide([Finding("font_inconsistent", ""), Finding("arithmetic_mismatch", "")])
    assert d.decision == "reject" and [r["rule"] for r in d.reasons] == ["R01", "R11"]
    assert rules.decide([]).decision == "auto_accept"
    assert rules.decide([Finding("pdf_modified_after_creation", "")]).decision == "auto_accept"   # info only
    base = {"version": "t", "default": "auto_accept"}
    with pytest.raises(RuleError, match="unknown finding code"):
        RuleSet.from_dict({**base, "rules": [{"id": "X", "when": {"any": ["made_up"]}, "decision": "reject"}]})
    with pytest.raises(RuleError, match="unknown decision"):
        RuleSet.from_dict({**base, "rules": [{"id": "X", "when": {"any": ["name_mismatch"]}, "decision": "approve"}]})
    combo = RuleSet.from_dict({**base, "rules": [
        {"id": "A", "when": {"all": ["font_inconsistent", "pdf_editor_producer"]}, "decision": "reject"},
        {"id": "B", "when": {"count": {"source": "forensics", "at_least": 2}}, "decision": "manual_review"}]})
    assert combo.decide([Finding("font_inconsistent", "")]).decision == "auto_accept"
    assert combo.decide([Finding("font_inconsistent", ""), Finding("pdf_incremental_update", "")]).decision == "manual_review"
    assert combo.decide([Finding("font_inconsistent", ""), Finding("pdf_editor_producer", "")]).decision == "reject"


def test_every_actionable_finding_has_a_rule():
    rules = RuleSet.load("rules/document_rules.yaml")
    actionable = {c for c, (_, sev, _) in CODES.items() if sev != "info"}
    assert actionable - rules.referenced_codes() == set()


# ------------------------------------------------------------------------------------------- verifier
def test_verifier_end_to_end_decisions(cfg, small):
    v = DocumentVerifier(cfg)
    for sub in small:   # genuine first, so duplicates see their originals
        verdict = v.verify(sub.pdf, sub.application)
        if sub.label in ("arithmetic", "invalid_nric", "period_mismatch", "name_mismatch", "duplicate_resubmission"):
            assert verdict.decision == "reject", (sub.id, sub.label, verdict.codes)
        if sub.label in ("font_inconsistency", "metadata_modified"):
            assert verdict.decision != "auto_accept", (sub.id, sub.label)


def test_verifier_fails_closed_when_a_check_breaks(cfg, small, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(checks, "fonts", boom)
    sub = _first(small, "payslip", template="P2")
    verdict = DocumentVerifier(cfg).verify(sub.pdf, sub.application)
    assert "check_error" in verdict.codes and verdict.decision == "manual_review"


def test_unreadable_and_unsupported_documents_go_to_review(cfg, small):
    app = _first(small, "payslip").application
    assert DocumentVerifier(cfg).verify(b"not a pdf", app).decision == "manual_review"
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.cell(0, 10, "Dear customer, thank you for your letter.")
    verdict = DocumentVerifier(cfg).verify(bytes(pdf.output()), app)
    assert verdict.codes == ["unsupported_document"] and verdict.decision == "manual_review"


def test_decision_logs_and_json_carry_no_pii(cfg, small, caplog):
    sub = _first(small, "payslip")
    with caplog.at_level(logging.INFO, logger="bankrag.docverify"):
        verdict = DocumentVerifier(cfg).verify(sub.pdf, sub.application)
    logged = " ".join(str(r.__dict__) for r in caplog.records)
    assert sub.truth["nric"] not in logged and sub.truth["employee_name"] not in logged
    assert sub.truth["nric"] not in json.dumps(verdict.to_json())
    assert verdict.to_json()["fields"]["nric"].endswith(sub.truth["nric"][-4:])


def test_anomaly_model_is_reproducible_and_explains(cfg, small):
    m1, m2 = AnomalyModel.load("data/docverify/anomaly_model.json"), AnomalyModel.load("data/docverify/anomaly_model.json")
    assert "dev split" in m1.spec["trained_on"]
    sub = _first(small, "payslip")
    assert m1.score("payslip", sub.truth) == m2.score("payslip", sub.truth)
    forged = {**sub.truth, "gross_pay": sub.truth["gross_pay"] * 3, "pcb": 0.0}
    score, thr, why = m1.score("payslip", forged)
    assert score > thr and "pcb_vs_schedule" in why


# ------------------------------------------------------------------------------------------- LLM extractor
class FakeLLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        content = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _llm_reply(truth):
    return json.dumps({**truth, "nric": None})


def test_llm_extractor_validated_output_and_nric_never_sent(cfg, small):
    sub = _first(small, "payslip", template="P3")
    fake = FakeLLM(["{not json", _llm_reply(sub.truth)])
    verdict = DocumentVerifier(cfg, extractor=LLMExtractor(fake, "test-model")).verify(sub.pdf, sub.application)
    assert verdict.extraction_method == "llm" and len(fake.calls) == 2       # retried after invalid JSON
    assert _norm(verdict.fields["nric"]) == _norm(sub.truth["nric"])           # filled locally
    sent = json.dumps([c["messages"] for c in fake.calls])
    assert sub.truth["nric"] not in sent and "[NRIC]" in sent
    assert fake.calls[0]["temperature"] == 0 and fake.calls[0]["response_format"] == {"type": "json_object"}


def test_llm_extractor_rejects_invented_values(cfg, small):
    sub = _first(small, "payslip")
    invented = json.dumps({**sub.truth, "nric": None, "net_pay": round(sub.truth["net_pay"] + 1000, 2)})
    fake = FakeLLM([invented, invented])
    verdict = DocumentVerifier(cfg, extractor=LLMExtractor(fake, "m")).verify(sub.pdf, sub.application)
    assert "not printed in the document" in fake.calls[1]["messages"][-1]["content"]
    assert verdict.decision == "manual_review" and "extraction_incomplete" in verdict.codes
