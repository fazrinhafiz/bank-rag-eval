"""Write a few synthetic documents to docs/samples/ for the README and the container smoke test.

    python scripts/make_doc_samples.py

samples.json lists each file with its application and the decision the verifier gives it (verified here,
in submission order, with a fresh duplicate registry - the same way a fresh container sees them).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bankrag.config import load_config  # noqa: E402
from bankrag.docverify.evaluate import corpus  # noqa: E402
from bankrag.docverify.verify import DocumentVerifier  # noqa: E402

PICKS = [  # (file name, doc_type, label, template, variant or None)
    ("genuine_payslip.pdf", "payslip", "genuine", "P1", "plain"),
    ("genuine_statement_malay.pdf", "bank_statement", "genuine", "S3", "plain"),
    ("tampered_payslip_arithmetic.pdf", "payslip", "arithmetic", "P1", None),
    ("tampered_payslip_consistent_forgery.pdf", "payslip", "consistent_forgery", "P2", None),
    ("tampered_statement_font.pdf", "bank_statement", "font_inconsistency", "S2", None),
    ("tampered_payslip_edited_metadata.pdf", "payslip", "metadata_modified", None, "editor_incremental"),
]

if __name__ == "__main__":
    cfg = load_config()
    subs = corpus(cfg, "test")
    out = ROOT / "docs/samples"
    out.mkdir(parents=True, exist_ok=True)
    verifier = DocumentVerifier(cfg)
    manifest = []
    for name, doc_type, label, template, variant in PICKS:
        s = next(x for x in subs if x.doc_type == doc_type and x.label == label
                 and (template is None or x.template == template) and (variant is None or x.variant == variant))
        v = verifier.verify(s.pdf, s.application)
        (out / name).write_bytes(s.pdf)
        manifest.append({"file": name, "corpus_id": s.id, "label": label, "variant": s.variant, "application": s.application,
                         "expected_decision": v.decision, "reasons": [r["reason"] for r in v.reasons]})
        print(f"{name:<42} {label:<20} -> {v.decision:<14} {', '.join(v.codes)}")
    (out / "samples.json").write_text(json.dumps(manifest, indent=2) + "\n")
