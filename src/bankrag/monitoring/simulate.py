"""Simulated production traffic for the drift demo.

Three windows, each run through the real pipeline so the traces are genuine:
  baseline  - normal mix: questions the KB covers, a few out-of-scope ones, rare injections
  stable    - same mix, different random seed (the monitor must NOT alarm here)
  drifted   - same mix plus a surge of home-loan / fixed-deposit questions the KB does not cover
"""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

from ..config import load_config, resolve
from ..pipeline import Pipeline
from ..tracing import Tracer, read_traces
from .drift import monitor, report_markdown

PREFIXES = ["", "", "Hi, ", "Hello. ", "Quick question: ", "Can you help me? ", "Good morning, "]
SUFFIXES = ["", "", " Thanks.", " Thank you!", " Please advise."]

HOME_LOAN_SUBJECTS = ["home loan", "mortgage", "housing loan", "property financing", "home refinancing"]
HOME_LOAN_ASKS = [
    "What is the interest rate for a {}?",
    "How much can I borrow with a {}?",
    "What is the maximum tenure for a {}?",
    "Do you offer a {} for first-time buyers?",
    "Is there a lock-in period penalty on a {}?",
    "Can I apply for a {} online?",
    "What documents do I need for a {}?",
    "What is the margin of financing for a {} on a condo?",
]
DEPOSIT_ASKS = [
    "What is the rate for a 6-month fixed deposit?",
    "Can I withdraw my fixed deposit early?",
    "What is the minimum placement for a fixed deposit?",
]


def _golden(cfg) -> list[dict]:
    return [json.loads(l) for l in resolve(cfg, "golden_set").read_text().splitlines() if l.strip()]


def _dress(rng: random.Random, question: str) -> str:
    return rng.choice(PREFIXES) + question + rng.choice(SUFFIXES)


def generate_traffic(cfg, n: int, seed: int, drift_share: float = 0.0) -> list[str]:
    rng = random.Random(seed)
    golden = _golden(cfg)
    covered = [g["question"] for g in golden if g["category"] in ("answerable", "pii")]
    uncovered = [g["question"] for g in golden if g["category"] == "unanswerable"]
    attacks = [g["question"] for g in golden if g["category"] == "injection"]
    out = []
    for _ in range(n):
        r = rng.random()
        if r < drift_share:
            q = rng.choice(HOME_LOAN_ASKS).format(rng.choice(HOME_LOAN_SUBJECTS)) if rng.random() < 0.8 else rng.choice(DEPOSIT_ASKS)
        elif r < drift_share + (1 - drift_share) * 0.08:
            q = rng.choice(uncovered)
        elif r < drift_share + (1 - drift_share) * 0.10:
            q = rng.choice(attacks)
        else:
            q = rng.choice(covered)
        out.append(_dress(rng, q))
    return out


def run_window(pipe: Pipeline, questions: list[str], path: Path) -> list[dict]:
    path.unlink(missing_ok=True)
    pipe.tracer = Tracer(path)
    for q in questions:
        pipe.ask(q)
    return read_traces(path)


def is_drift_query(masked_query: str) -> bool:
    q = masked_query.lower()
    return any(s in q for s in HOME_LOAN_SUBJECTS) or "fixed deposit" in q


def _reading(drifted: list[dict], rep) -> str:
    surge = [t for t in drifted if is_drift_query(t["masked_query"])]
    answered = [t for t in surge if not (t["abstained"] or t["blocked"])]
    docs = Counter(t["top_doc"] for t in answered).most_common(3)
    pii = next(r for r in rep.rates if r.name == "pii_rate")
    return "\n".join([
        f"- {len(surge)} of {len(drifted)} drifted-window requests were surge questions. The pipeline abstained on "
        f"{len(surge) - len(answered)} ({(len(surge) - len(answered)) / max(len(surge), 1):.0%}) of them, which is what pushes the abstention rate up.",
        f"- It **answered the other {len(answered)}** from the nearest covered section "
        f"({', '.join(f'{d}: {n}' for d, n in docs)}). Those answers cite real policy text, but it is the wrong product. This is the same "
        "failure the offline eval shows on una-03 / una-08 / una-10. Drift monitoring is the production safety net for "
        "out-of-scope questions that the abstain threshold lets through.",
        f"- The pii_rate change ({pii.baseline:.1%} -> {pii.current:.1%}) is a **mix effect**, not a privacy event: the surge questions contain no PII, "
        "so the share of PII-bearing requests is diluted. A real alert triage would note it and close it.",
        "- Recommended response: route home-loan and fixed-deposit intents to a human or a static 'not available in this channel' reply, and "
        "raise a change request to add the mortgage product terms to the knowledge base. Re-run the quality gate after ingesting them.",
    ])


def main(n: int = 500, drift_share: float = 0.40, out_dir: str | None = None) -> tuple:
    cfg = load_config()
    trace_dir = Path(out_dir) if out_dir else resolve(cfg, "trace_path").parent / "simulation"
    pipe = Pipeline(cfg)
    baseline = run_window(pipe, generate_traffic(cfg, n, seed=1), trace_dir / "baseline.jsonl")
    stable = run_window(pipe, generate_traffic(cfg, n, seed=2), trace_dir / "current_stable.jsonl")
    drifted = run_window(pipe, generate_traffic(cfg, n, seed=3, drift_share=drift_share), trace_dir / "current_drifted.jsonl")

    stable_rep, drift_rep = monitor(baseline, stable), monitor(baseline, drifted)
    text = "\n".join([
        "# Monitoring report: simulated drift",
        "",
        "Generated by `python scripts/simulate_drift.py`. Every trace comes from running the real pipeline",
        f"(retrieval mode `{pipe.retriever.mode}`, threshold {pipe.threshold}) on synthetic traffic with fixed seeds.",
        f"The drifted window adds a {drift_share:.0%} surge of home-loan and fixed-deposit questions, which the knowledge base does not cover.",
        "",
        "PSI bands: < 0.10 stable · 0.10-0.25 moderate · > 0.25 significant. KS p-values are asymptotic.",
        "",
        report_markdown(stable_rep, "Control: baseline vs a second normal window (expect no alert)"),
        report_markdown(drift_rep, "Drift: baseline vs home-loan surge (expect alerts)"),
        "## Reading the result",
        "",
        _reading(drifted, drift_rep),
        "",
    ])
    return text, stable_rep, drift_rep
