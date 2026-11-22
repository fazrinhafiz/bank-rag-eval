"""Evaluate document verification on the synthetic corpus, calibrate the anomaly model on DEV, gate on TEST.

    bankrag doc-calibrate          fit the anomaly model on genuine dev documents, write its threshold
    bankrag doc-eval [--split]     run every document through the verifier, write reports, enforce gates

The dev split is the only one used to build anything (anomaly model and its threshold). The test split has
different people and seeds and one payslip and one statement layout (P3, S3) that dev never contains.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..config import resolve
from ..eval.harness import source_fingerprint
from ..eval.metrics import auroc
from . import anomaly as anomaly_mod
from .extract import extract, read_pdf
from .synth import TAMPER_DESCRIPTIONS, TAMPER_KINDS, Submission, build_corpus, corpus_hash
from .verify import DocumentVerifier

PREVALENCE = 0.02     # assumed share of tampered documents in real intake, for the "expected" rates
# Tampers that change amounts while keeping them consistent: what the anomaly score exists for.
AMOUNT_KINDS = ("consistent_forgery", "font_inconsistency", "metadata_modified")


def corpus(cfg: dict, split: str) -> list[Submission]:
    c = cfg["docverify"]["corpus"]
    return build_corpus(split, c[f"{split}_seed"], c["genuine_per_type"], c["tampered_per_kind"])


def calibrate(cfg: dict) -> dict:
    """Fit on genuine DEV documents only, using the fields the extractor actually reads from them."""
    dev = corpus(cfg, "dev")
    genuine: dict[str, list] = defaultdict(list)
    tampered: dict[str, list] = defaultdict(list)   # amount-changing tampers, used only to choose the method
    for s in dev:
        if s.tampered and s.label not in AMOUNT_KINDS:
            continue
        ex = extract(read_pdf(s.pdf))
        x = anomaly_mod.features(ex.doc_type, ex.fields) if ex.doc_type and not ex.missing else None
        if x is not None:
            (tampered if s.tampered else genuine)[ex.doc_type].append(x)
    spec = anomaly_mod.fit(dict(genuine), dict(tampered), cfg["docverify"]["anomaly_quantile"],
                           f"genuine documents of the dev split (seed {cfg['docverify']['corpus']['dev_seed']}, corpus {corpus_hash(dev)})")
    out = resolve(cfg, cfg["docverify"]["anomaly_model"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=1) + "\n")
    return spec


def _norm(v):
    return " ".join(v.upper().split()) if isinstance(v, str) else v


def _extraction_counts(truth: dict, got: dict) -> tuple[int, int, int]:
    tp = fp = fn = 0
    for k, v in truth.items():
        g = got.get(k)
        if g is None or g == []:
            fn += 1
        elif _norm(g) == _norm(v):
            tp += 1
        else:
            fp += 1
            fn += 1
    return tp, fp, fn


def _prf(tp, fp, fn) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0, "n_fields": tp + fn}


@dataclass
class DocResult:
    id: str
    doc_type: str
    label: str
    variant: str
    template: str
    decision: str
    codes: list
    anomaly_score: float | None
    extraction: tuple
    reasons: list

    def to_json(self) -> dict:
        return {**self.__dict__, "extraction": list(self.extraction)}


def run(cfg: dict, split: str, disabled: set[str] = frozenset(), anomaly=True) -> dict:
    subs = corpus(cfg, split)
    verifier = DocumentVerifier(cfg, anomaly=anomaly, disabled=disabled)
    results = []
    for s in subs:
        v = verifier.verify(s.pdf, s.application)
        results.append(DocResult(s.id, s.doc_type, s.label, s.variant, s.template, v.decision, v.codes, v.anomaly_score,
                                 _extraction_counts(s.truth, v.fields), [r["rule"] for r in v.reasons]))
    meta = {"split": split, "n_documents": len(subs), "corpus_hash": corpus_hash(subs), "rules_version": verifier.rules.version,
            "anomaly_model": None if verifier.anomaly is None else verifier.anomaly.spec["trained_on"],
            "disabled_checks": sorted(disabled), "source_fingerprint": source_fingerprint(cfg["_root"]),
            "prevalence_assumed": PREVALENCE}
    return {"metadata": meta, "metrics": metrics(results), "results": [r.to_json() for r in results]}


def _rate(xs, pred) -> float:
    xs = list(xs)
    return sum(1 for x in xs if pred(x)) / len(xs) if xs else 0.0


def metrics(results: list[DocResult]) -> dict:
    gen = [r for r in results if r.label == "genuine"]
    tam = [r for r in results if r.label != "genuine"]
    m: dict = {"n_genuine": len(gen), "n_tampered": len(tam)}
    m["genuine_auto_accept_rate"] = _rate(gen, lambda r: r.decision == "auto_accept")
    m["genuine_review_rate"] = _rate(gen, lambda r: r.decision == "manual_review")
    m["genuine_reject_rate"] = _rate(gen, lambda r: r.decision == "reject")
    m["tamper_detection_rate"] = _rate(tam, lambda r: r.decision != "auto_accept")
    m["tamper_reject_rate"] = _rate(tam, lambda r: r.decision == "reject")
    for kind in TAMPER_KINDS:
        m[f"recall_{kind}"] = _rate([r for r in tam if r.label == kind], lambda r: r.decision != "auto_accept")
    # Decided by the machine, and how often that automatic decision was wrong, on the corpus mix.
    auto = [r for r in results if r.decision != "manual_review"]
    m["automation_rate"] = len(auto) / len(results) if results else 0.0
    wrong = [r for r in auto if (r.label == "genuine") != (r.decision == "auto_accept")]
    m["auto_decision_error_rate"] = len(wrong) / len(auto) if auto else 0.0
    # The same at an assumed real-world prevalence, tamper kinds weighted equally.
    kinds = [k for k in TAMPER_KINDS if any(r.label == k for r in tam)]
    t_auto = sum(_rate([r for r in tam if r.label == k], lambda r: r.decision != "manual_review") for k in kinds) / len(kinds)
    t_acc = sum(m[f"recall_{k}"] for k in kinds) / len(kinds)
    g_auto = m["genuine_auto_accept_rate"] + m["genuine_reject_rate"]
    p = PREVALENCE
    m["expected_automation_rate"] = (1 - p) * g_auto + p * t_auto
    m["expected_auto_error_rate"] = ((1 - p) * m["genuine_reject_rate"] + p * (1 - t_acc)) / m["expected_automation_rate"] \
        if m["expected_automation_rate"] else 0.0
    tp, fp, fn = (sum(r.extraction[i] for r in results) for i in range(3))
    ext = _prf(tp, fp, fn)
    m["extraction_precision"], m["extraction_recall"], m["extraction_f1"] = ext["precision"], ext["recall"], ext["f1"]
    m["extraction_doc_exact_rate"] = _rate(results, lambda r: r.extraction[1] == 0 and r.extraction[2] == 0)
    scored = [r for r in results if r.anomaly_score is not None and (r.label == "genuine" or r.label in AMOUNT_KINDS)]
    m["anomaly_auroc"] = auroc([r.anomaly_score for r in scored], [r.label != "genuine" for r in scored])
    return m


def breakdowns(results: list[dict]) -> dict:
    out: dict = {"by_kind": {}, "by_template": {}, "codes_by_kind": {}, "extraction_by_template": {}}
    for kind in ("genuine", *TAMPER_KINDS):
        rs = [r for r in results if r["label"] == kind]
        if not rs:
            continue
        for dt in ("payslip", "bank_statement"):
            sub = [r for r in rs if r["doc_type"] == dt]
            if sub:
                out["by_kind"][f"{kind}|{dt}"] = dict(Counter(r["decision"] for r in sub)) | {"n": len(sub)}
        out["codes_by_kind"][kind] = dict(Counter(c for r in rs for c in r["codes"]).most_common())
    for t in sorted({r["template"] for r in results}):
        rs = [r for r in results if r["template"] == t]
        g = [r for r in rs if r["label"] == "genuine"]
        tam = [r for r in rs if r["label"] != "genuine"]
        out["by_template"][t] = {"n_genuine": len(g), "genuine_auto_accept": _rate(g, lambda r: r["decision"] == "auto_accept"),
                                 "n_tampered": len(tam), "detected": _rate(tam, lambda r: r["decision"] != "auto_accept")}
        tp, fp, fn = (sum(r["extraction"][i] for r in rs) for i in range(3))
        out["extraction_by_template"][t] = _prf(tp, fp, fn)
    return out


# --------------------------------------------------------------------------------------------- report
def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.1%}"


def report_markdown(test: dict, dev: dict, gates: list, model: dict | None) -> str:
    m, md, meta = test["metrics"], dev["metrics"], test["metadata"]
    b = breakdowns(test["results"])
    L = ["# Document verification evaluation", "",
         f"Rules `{meta['rules_version']}` · source fingerprint `{meta['source_fingerprint']}` · test corpus `{meta['corpus_hash']}` "
         f"({meta['n_documents']} documents: {m['n_genuine']} genuine, {m['n_tampered']} tampered) · dev corpus `{dev['metadata']['corpus_hash']}`.", "",
         "**All documents are synthetic** (`src/bankrag/docverify/synth.py`): fictional people, employers and banks, "
         "simplified payroll rules, and tampering simulated by the same author who wrote the checks. These numbers show "
         "the checks do what they were designed to do and stay correct as the code changes. They are **not** an estimate "
         "of performance on real documents - see the limitations at the end.", "",
         "## Gates (test split)", "", "| gate | value | threshold | result | why |", "|---|---|---|---|---|"]
    for g in gates:
        L.append(f"| {g.metric} | {_pct(g.value)} | {g.op} {_pct(g.threshold)} | {'PASS' if g.passed else '**FAIL**'} | {g.why} |")
    L += ["", "## Headline", "", "| metric | test | dev |", "|---|---|---|"]
    for key, label in (("tamper_detection_rate", "Tampered documents not auto-accepted"),
                       ("tamper_reject_rate", "Tampered documents auto-rejected"),
                       ("genuine_auto_accept_rate", "Genuine documents auto-accepted"),
                       ("genuine_review_rate", "Genuine documents sent to manual review"),
                       ("genuine_reject_rate", "Genuine documents wrongly rejected"),
                       ("automation_rate", "Automation rate (corpus mix)"),
                       ("auto_decision_error_rate", "Error rate of automatic decisions (corpus mix)"),
                       ("expected_automation_rate", f"Automation rate at {PREVALENCE:.0%} fraud prevalence"),
                       ("expected_auto_error_rate", f"Error rate of automatic decisions at {PREVALENCE:.0%} prevalence"),
                       ("extraction_f1", "Field extraction F1"), ("extraction_doc_exact_rate", "Documents with every field exact"),
                       ("anomaly_auroc", "Anomaly score AUROC (genuine vs amount-changing tampers)")):
        L.append(f"| {label} | **{_pct(m[key]) if key != 'anomaly_auroc' else (f'{m[key]:.3f}' if m[key] is not None else 'n/a')}** | "
                 f"{_pct(md[key]) if key != 'anomaly_auroc' else (f'{md[key]:.3f}' if md[key] is not None else 'n/a')} |")
    L += ["", "## Recall by tamper kind (test)", "",
          "Recall = share NOT auto-accepted (sent to review or rejected).", "",
          "| kind | what was done | payslip | statement | recall | checks that fired |", "|---|---|---|---|---|---|"]
    for kind in TAMPER_KINDS:
        cells = []
        for dt in ("payslip", "bank_statement"):
            d = b["by_kind"].get(f"{kind}|{dt}")
            cells.append("-" if not d else f"{d['n'] - d.get('auto_accept', 0)}/{d['n']} ({d.get('reject', 0)} rejected)")
        codes = ", ".join(f"`{c}` {n}" for c, n in list(b["codes_by_kind"].get(kind, {}).items())[:4])
        L.append(f"| {kind} | {TAMPER_DESCRIPTIONS[kind]} | {cells[0]} | {cells[1]} | **{_pct(m[f'recall_{kind}'])}** | {codes} |")
    g = b["codes_by_kind"].get("genuine", {})
    L += ["", "## Genuine documents (test)", "",
          f"Findings on genuine documents: {', '.join(f'`{c}` {n}' for c, n in g.items()) or 'none'}. "
          "`pdf_modified_after_creation` is informational (a statement re-downloaded later) and triggers no rule.", "",
          "| layout | in dev? | genuine auto-accepted | tampered detected | extraction F1 |", "|---|---|---|---|---|"]
    for t, d in b["by_template"].items():
        held_out = t in ("P3", "S3")
        L.append(f"| {t} | {'**no (held out)**' if held_out else 'yes'} | {_pct(d['genuine_auto_accept'])} of {d['n_genuine']} | "
                 f"{_pct(d['detected'])} of {d['n_tampered']} | {b['extraction_by_template'][t]['f1']:.3f} |")
    misses = [r for r in test["results"] if r["label"] != "genuine" and r["decision"] == "auto_accept"]
    false_flags = [r for r in test["results"] if r["label"] == "genuine" and r["decision"] != "auto_accept"]
    L += ["", f"## Missed tampering: {len(misses)} auto-accepted", ""]
    if misses:
        L += ["| id | kind | variant | layout | anomaly score |", "|---|---|---|---|---|"]
        L += [f"| {r['id']} | {r['label']} | {r['variant']} | {r['template']} | "
              f"{'-' if r['anomaly_score'] is None else round(r['anomaly_score'], 3)} |" for r in misses]
    L += ["", f"## Genuine documents not auto-accepted: {len(false_flags)}", ""]
    if false_flags:
        L += ["| id | layout | variant | decision | findings |", "|---|---|---|---|---|"]
        L += [f"| {r['id']} | {r['template']} | {r['variant']} | {r['decision']} | {', '.join(r['codes'])} |" for r in false_flags]
    if model:
        L += ["", "## Anomaly model (fitted and selected on dev only)", "", f"Trained on {model['trained_on']}. "
              f"Threshold = {model['quantile']:.0%} quantile of out-of-fold scores of genuine dev documents. "
              "Method chosen per document type by recall on dev amount-changing tampers at that threshold (ties: AUROC).", "",
              "| doc type | features | method | dev recall (amount tampers) | dev AUROC | dev genuine flagged | selected |",
              "|---|---|---|---|---|---|---|"]
        for t, d in model["models"].items():
            for c in d["comparison"]:
                L.append(f"| {t} | {', '.join(d['features'])} | {c['method']} | {_pct(c['dev_tamper_recall'])} | "
                         f"{c['dev_auroc']:.3f} | {_pct(c['dev_genuine_flag_rate'])} | {'✅' if c['method'] == d['method'] else ''} |")
    L += ["", "## Limitations", "",
          "- Synthetic documents, synthetic tampering, same author for both: high recall on rule-detectable kinds is "
          "close to true by construction. The informative rows are the hard ones (consistent forgery), the held-out "
          "layouts, and the genuine documents sent to review.",
          "- The payslip anomaly feature `pcb_vs_schedule` was designed knowing the simulated forger leaves tax unchanged. "
          "A forger who also recomputes PCB defeats it; the 100% on payslip consistent forgeries is the upper bound of that design.",
          "- A bank statement whose arithmetic was kept consistent has nothing inside it to contradict: the anomaly score "
          "catches few of these. The fix is a cross-document check (salary credit on the statement vs net pay on the "
          "payslip) or data from the source (open banking, EPF/LHDN), not a better model of one document.",
          "- Process note: the test split was run once with the first anomaly design (IsolationForest on ratio features, "
          "0% of consistent forgeries on dev and test). The redesign and method choice used dev only; gates were written "
          "before this test run.",
          "- Text-layer PDFs only. Scans and photos have no text layer and go to manual review; OCR is not implemented.",
          "- Each tampered document carries one kind of tampering. Real forgeries often leave several traces, which "
          "makes them easier to catch, but real forgers also adapt to known checks.",
          "- Forensic checks are easy to evade once known (strip metadata, re-render the whole PDF in one font). They "
          "raise the cost of casual edits; they do not stop a determined forger.",
          "- Payroll rules are simplified. Real EPF/SOCSO/EIS tables and PCB have bands and exceptions, so the "
          "statutory check would need the official tables before production use.",
          f"- The expected rates assume {PREVALENCE:.0%} of submissions are tampered and every tamper kind is equally common."]
    return "\n".join(L) + "\n"
