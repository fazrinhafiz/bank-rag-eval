"""Generated result blocks for README.md and docs/MODEL_CARD.md.

Every number in the docs comes from the committed report JSON (reports/*.json), written by real runs of
`eval`, `benchmark` and `scripts/simulate_drift.py`. Blocks sit between markers:
    <!-- generated:NAME:start --> ... <!-- generated:NAME:end -->
`python scripts/update_doc_results.py` rewrites them; `--check` (and tests/test_docs_in_sync.py) fails if
the docs and the reports disagree. Latency is deliberately excluded: it varies run to run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROWS = [  # (metric key, label, format)
    ("hit_at_1", "Retrieval hit@1", "pct"), ("hit_at_3", "Retrieval hit@3", "pct"), ("mrr", "Retrieval MRR@3", "f3"),
    ("key_fact_recall", "Key-fact recall", "pct"), ("citation_accuracy", "Citation accuracy", "pct"),
    ("faithfulness", "Faithfulness (lexical)", "pct"), ("abstention_precision", "Abstention precision", "pct"),
    ("abstention_recall", "Abstention recall (unanswerable)", "pct"), ("confidence_auroc", "Confidence AUROC", "f3"),
    ("injection_block_rate", "Injection block rate", "pct"), ("false_block_rate", "False block rate", "pct"),
    ("pii_mask_rate", "PII masked", "pct"), ("pii_leak_rate", "PII leak rate", "pct"), ("task_success", "Task success (all items)", "pct"),
]


def _f(v, kind):
    return f"{v:.1%}" if kind == "pct" else f"{v:.3f}"


def _gate(spec, kind):
    if not spec:
        return "-"
    return f"≥ {_f(spec['min'], kind)}" if "min" in spec else f"≤ {_f(spec['max'], kind)}"


def results_block(root: Path) -> str:
    test = json.loads((root / "reports/eval_report.json").read_text())
    dev = json.loads((root / "reports/eval_report_dev.json").read_text())
    gates = yaml.safe_load((root / "eval/thresholds.yaml").read_text())["gates"]
    m = test["metadata"]
    lines = [f"Offline baseline: retrieval `{m['retrieval_mode']}`, generator `{m['model']}`, prompt `{m['prompt_version']}`, "
             f"abstain threshold `{m['abstain_threshold']}` (calibrated on dev).", "",
             f"| metric | **test** (n={test['metrics']['n_items']}, gated) | dev (n={dev['metrics']['n_items']}, used for calibration) | gate |",
             "|---|---|---|---|"]
    for key, label, kind in ROWS:
        lines.append(f"| {label} | **{_f(test['metrics'][key], kind)}** | {_f(dev['metrics'][key], kind)} | {_gate(gates.get(key), kind)} |")
    n_pass = sum(1 for r in test["results"] if r["passed"])
    cats = ", ".join(f"{c} {v['passed']}/{v['n']}" for c, v in test["categories"].items())
    failed = ", ".join(r["id"] for r in test["results"] if not r["passed"])
    lines += ["", f"Test split: {n_pass}/{len(test['results'])} items fully correct ({cats}). Failed: {failed or 'none'}. "
              "Every failure is explained in [reports/eval_report.md](reports/eval_report.md)."]
    return "\n".join(lines)


def benchmark_block(root: Path) -> str:
    data = json.loads((root / "reports/benchmark.json").read_text())
    lines = [f"| mode | dev abstain F1 | dev key-fact recall | hit@1 (all {data['rows'][0]['n_retrieval_items']}) | MRR (all) | "
             "test key-fact recall | test abstain P / R | test conf. AUROC | test task success |", "|---|---|---|---|---|---|---|---|---|"]
    for r in data["rows"]:
        star = " ✅" if r["mode"] == data["selected_on_dev"] else ""
        lines.append(f"| {r['mode']}{star} | {r['dev_abstention_f1']:.3f} | {r['dev_key_fact_recall']:.1%} | {r['all_hit_at_1']:.1%} | {r['all_mrr']:.3f} | "
                     f"{r['test_key_fact_recall']:.1%} | {r['test_abstention_precision']:.1%} / {r['test_abstention_recall']:.1%} | "
                     f"{r['test_confidence_auroc']:.3f} | {r['test_task_success']:.1%} |")
    hit3 = ", ".join(f"{r['all_hit_at_3']:.0%}" for r in data["rows"])
    lines += ["", f"✅ = selected on **dev** metrics only (`{data['selected_on_dev']}`). All three modes reach hit@3 = {hit3}."]
    return "\n".join(lines)


def drift_block(root: Path) -> str:
    s = json.loads((root / "reports/monitoring_summary.json").read_text())
    c, d, rep = s["control"], s["drifted"], s["repeated"]
    lines = ["| window | status | PSI confidence | PSI query length | PSI topic mix | PSI query terms | abstention rate |",
             "|---|---|---|---|---|---|---|"]
    for name, w in (("control (normal mix, new seed)", c), ("drifted (40% top-up surge)", d)):
        a = w["rates"]["abstained_rate"]
        lines.append(f"| {name} | **{w['status']}** | {w['psi']['retrieval_confidence']:.3f} | {w['psi']['query_length']:.3f} | "
                     f"{w['psi']['topic_mix']:.3f} | {w['psi']['query_terms']:.3f} | {a[0]:.1%} → {a[1]:.1%} |")
    rc, rd = rep["control"], rep["drifted"]
    lines += ["", f"Real Banking77 queries and deepset injection attempts, {d['n_current']} per window, run through the real pipeline. "
              f"Repeated over {rc['repeats']} seeds: control windows gave {rc['OK']} OK / {rc['WATCH']} WATCH / {rc['ALERT']} ALERT; "
              f"drifted windows gave {rd['OK']} OK / {rd['WATCH']} WATCH / {rd['ALERT']} ALERT. "
              "Details: [reports/monitoring_report.md](reports/monitoring_report.md)."]
    return "\n".join(lines)


def external_block(root: Path) -> str:
    x = json.loads((root / "reports/external_eval.json").read_text())
    b, j = x["banking77"], x["injections"]
    golden = json.loads((root / "reports/eval_report.json").read_text())["metrics"]
    lines = ["| measure | golden set (test, written by the author) | independent public data |", "|---|---|---|",
             f"| Out-of-scope questions refused | {golden['abstention_recall']:.1%} (6 near-miss items) | **{b['oos_abstention_rate']:.1%}** of {b['n_by_label']['not_covered']:,} Banking77 queries from {b['n_intents']['not_covered']} uncovered intents |",
             f"| In-scope questions answered | {golden['answer_rate']:.1%} | **{b['in_scope_answer_rate']:.1%}** of {b['n_by_label']['covered']} Banking77 queries from {b['n_intents']['covered']} covered intents |",
             f"| In-scope retrieval hit@3 | {golden['hit_at_3']:.1%} | **{b['in_scope_hit_at_3']:.1%}** |",
             f"| Legitimate questions blocked as injection | {golden['false_block_rate']:.1%} | **{b['false_block_rate']:.1%}** of {b['n_queries']:,} Banking77 queries; {j['all']['false_positive_rate']:.1%} of {j['all']['n_benign']} deepset benign prompts |",
             f"| Injection attempts blocked | {golden['injection_block_rate']:.1%} (4 items) | **{j['english']['detection_rate']:.1%}** of {j['english']['n_injection']} English deepset injections ({j['all']['detection_rate']:.1%} of all {j['all']['n_injection']}, incl. German) |",
             "", "Details: [reports/external_eval.md](reports/external_eval.md)."]
    return "\n".join(lines)


BLOCKS = {"results": results_block, "benchmark": benchmark_block, "drift": drift_block, "external": external_block}
DOCS = ("README.md", "docs/MODEL_CARD.md")
_MARKER = re.compile(r"<!-- generated:(\w+):start -->\n(?:.*?\n)?<!-- generated:\1:end -->", re.S)


def render(text: str, root: Path) -> str:
    def block(m):
        name = m.group(1)
        return f"<!-- generated:{name}:start -->\n{BLOCKS[name](root)}\n<!-- generated:{name}:end -->"
    return _MARKER.sub(block, text)


def sync(root: Path, check: bool = False) -> list[str]:
    stale = []
    for rel in DOCS:
        path = root / rel
        if not path.exists():
            continue
        old = path.read_text()
        new = render(old, root)
        if new != old:
            stale.append(rel)
            if not check:
                path.write_text(new)
    return stale
