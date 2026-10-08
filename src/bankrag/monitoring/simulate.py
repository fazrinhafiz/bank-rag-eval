"""Simulated production traffic for the drift demo, built from REAL customer queries (Banking77) and
real injection attempts (deepset/prompt-injections). Only the traffic mix is simulated.

Three windows, each run through the real pipeline so the traces are genuine:
  baseline  - 85% queries from intents the KB covers (fully or partly), 13% from uncovered intents,
              2% English injection attempts
  stable    - same mix, different random seed (the monitor must NOT alarm here)
  drifted   - same mix with a 40% surge of top-up questions, as after a top-up outage or a new
              top-up feature. The KB has no top-up policy.
"""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

from ..config import load_config, resolve
from ..eval.external import EXT, is_german, load_intent_map
from ..pipeline import Pipeline
from ..tracing import Tracer, read_traces
from .drift import monitor, report_markdown

SURGE_INTENTS = {"pending_top_up", "top_up_failed", "top_up_reverted", "topping_up_by_card", "top_up_limits",
                 "automatic_top_up", "verify_top_up", "top_up_by_card_charge", "top_up_by_bank_transfer_charge",
                 "top_up_by_cash_or_cheque"}
MIX = {"in_scope": 0.85, "out_of_scope": 0.13, "injection": 0.02}


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def traffic_pools(root: Path) -> dict[str, list[str]]:
    imap = load_intent_map(root)
    b77 = _jsonl(root / EXT / "banking77_test.jsonl")
    attacks = [r["text"] for r in _jsonl(root / EXT / "deepset_prompt_injections.jsonl")
               if r["is_injection"] and not is_german(r["text"])]
    return {
        "in_scope": [r["text"] for r in b77 if imap[r["intent"]]["label"] in ("covered", "ambiguous")],
        "out_of_scope": [r["text"] for r in b77 if imap[r["intent"]]["label"] == "not_covered" and r["intent"] not in SURGE_INTENTS],
        "injection": attacks,
        "surge": [r["text"] for r in b77 if r["intent"] in SURGE_INTENTS],
    }


def generate_traffic(pools: dict, n: int, seed: int, drift_share: float = 0.0) -> list[tuple[str, bool]]:
    """Returns (query, is_surge) pairs."""
    rng = random.Random(seed)
    kinds, weights = list(MIX), list(MIX.values())
    out = []
    for _ in range(n):
        if rng.random() < drift_share:
            out.append((rng.choice(pools["surge"]), True))
        else:
            out.append((rng.choice(pools[rng.choices(kinds, weights)[0]]), False))
    return out


def run_window(pipe: Pipeline, traffic: list[tuple[str, bool]], path: Path) -> list[dict]:
    path.unlink(missing_ok=True)
    pipe.tracer = Tracer(path)
    for q, _ in traffic:
        pipe.ask(q)
    return read_traces(path)


def _reading(drifted: list[dict], surge_flags: list[bool], rep) -> str:
    surge = [t for t, s in zip(drifted, surge_flags) if s]
    answered = [t for t in surge if not (t["abstained"] or t["blocked"])]
    docs = Counter(t["top_doc"] for t in answered).most_common(3)
    pii = next(r for r in rep.rates if r.name == "pii_rate")
    lines = [
        f"- {len(surge)} of {len(drifted)} drifted-window requests were surge (top-up) questions. The pipeline abstained on "
        f"{len(surge) - len(answered)} ({(len(surge) - len(answered)) / max(len(surge), 1):.0%}) of them, which pushes the abstention rate up.",
        f"- It **answered the other {len(answered)}** from the nearest covered section "
        f"({', '.join(f'{d}: {n}' for d, n in docs) or 'none'}). Those answers cite real policy text about a different product. "
        "Drift monitoring is the production safety net for out-of-scope questions that the abstain threshold lets through.",
    ]
    if pii.alert:
        lines.append(f"- The pii_rate change ({pii.baseline:.1%} -> {pii.current:.1%}) is a mix effect, not a privacy event.")
    lines.append("- Recommended response: route top-up intents to a human or a static 'not available in this channel' reply, and "
                 "check whether a top-up policy is missing from the knowledge base. Re-run the quality gate after any document change.")
    return "\n".join(lines)


def repeated_check(pipe: Pipeline, pools: dict, trace_dir: Path, n: int, drift_share: float, repeats: int = 20) -> dict:
    """Repeat both scenarios with fresh seeds for the baseline AND the current window, so the result does
    not hinge on one particular baseline sample. Reports how often each status occurs."""
    out = {}
    tmp = trace_dir / "repeat.jsonl"
    for name, share, seed0 in (("control", 0.0, 100), ("drifted", drift_share, 1100)):
        reps = []
        for i in range(repeats):
            base = run_window(pipe, generate_traffic(pools, n, seed0 + 500 + i), tmp)
            reps.append(monitor(base, run_window(pipe, generate_traffic(pools, n, seed0 + i, share), tmp)))
        psi = [next(f.psi for f in r.features if f.name == "query_terms") for r in reps]
        out[name] = {"repeats": repeats, **{s: sum(r.status == s for r in reps) for s in ("OK", "WATCH", "ALERT")},
                     "query_terms_psi_min": round(min(psi), 3), "query_terms_psi_max": round(max(psi), 3)}
    tmp.unlink(missing_ok=True)
    return out


def _history(drift_rep) -> str:
    """Show, on the same windows, what the monitor would say without the query-terms signal."""
    without = [f for f in drift_rep.features if f.name != "query_terms"]
    rates = [r for r in drift_rep.rates if r.alert]
    status = "ALERT" if any(f.band == "significant" for f in without) or rates else "WATCH" if any(f.band == "moderate" for f in without) else "OK"
    psi = {f.name: f.psi for f in drift_rep.features}
    ab = next(r for r in drift_rep.rates if r.name == "abstained_rate")
    return "\n".join([
        "The first version monitored retrieval confidence, query length, answered topic and behavioural rates only. On these same "
        f"windows it would report the top-up surge as **{status}** (confidence PSI {psi['retrieval_confidence']:.3f}, topic-mix PSI "
        f"{psi['topic_mix']:.3f}, abstention {ab.baseline:.1%} -> {ab.current:.1%}). Real phrasing is refused so often that one kind of "
        "unanswered question looks like another.",
        "",
        "Two changes followed, both made after seeing results on this simulated traffic, so the repeated-seed check above is the fair "
        "measure of how the monitor behaves:",
        "1. A `query_terms` feature: PSI over the content terms of the PII-masked queries.",
        "2. Each query now counts once in that feature. When every term occurrence counted, one long jailbreak prompt in the baseline "
        "window (about 1% of all terms) pushed every control window to WATCH. The repeated check also draws a fresh baseline each time.",
    ])


def main(n: int = 500, drift_share: float = 0.40, out_dir: str | None = None) -> tuple:
    cfg = load_config()
    root = Path(cfg["_root"])
    trace_dir = Path(out_dir) if out_dir else resolve(cfg, "trace_path").parent / "simulation"
    pipe = Pipeline(cfg)
    pools = traffic_pools(root)
    base_t, stable_t = generate_traffic(pools, n, seed=1), generate_traffic(pools, n, seed=2)
    drift_t = generate_traffic(pools, n, seed=3, drift_share=drift_share)
    baseline = run_window(pipe, base_t, trace_dir / "baseline.jsonl")
    stable = run_window(pipe, stable_t, trace_dir / "current_stable.jsonl")
    drifted = run_window(pipe, drift_t, trace_dir / "current_drifted.jsonl")

    stable_rep, drift_rep = monitor(baseline, stable), monitor(baseline, drifted)
    rep = repeated_check(pipe, pools, trace_dir, n, drift_share)
    rep_lines = ["## Repeated-seed check", "",
                 f"One window passing proves little, so both scenarios are re-run {rep['control']['repeats']} times, each with a fresh "
                 "baseline window and a fresh current window (different seeds).", "",
                 "| scenario | OK | WATCH | ALERT | query_terms PSI range |", "|---|---|---|---|---|"]
    for name, r in rep.items():
        rep_lines.append(f"| {name} | {r['OK']} | {r['WATCH']} | {r['ALERT']} | {r['query_terms_psi_min']:.3f} - {r['query_terms_psi_max']:.3f} |")
    text = "\n".join([
        "# Monitoring report: simulated drift on real customer queries",
        "",
        "Generated by `python scripts/simulate_drift.py`. Every request is a real customer query from Banking77 or a real",
        "injection attempt from deepset/prompt-injections, run through the real pipeline "
        f"(retrieval mode `{pipe.retriever.mode}`, threshold {pipe.threshold}). Only the traffic mix and the surge are simulated, with fixed seeds.",
        f"Baseline mix: {MIX['in_scope']:.0%} in-scope intents (covered or partly covered), {MIX['out_of_scope']:.0%} uncovered intents, "
        f"{MIX['injection']:.0%} English injection attempts. The drifted window adds a {drift_share:.0%} surge of top-up questions "
        f"({len(SURGE_INTENTS)} Banking77 top-up intents), which the knowledge base does not cover.",
        "",
        "PSI bands: < 0.10 stable · 0.10-0.25 moderate · > 0.25 significant. KS p-values are asymptotic.",
        "",
        report_markdown(stable_rep, "Control: baseline vs a second normal window (expect no alert)"),
        report_markdown(drift_rep, "Drift: baseline vs top-up surge (expect alerts)"),
        *rep_lines,
        "",
        "## How the monitor changed after meeting real traffic",
        "",
        _history(drift_rep),
        "",
        "## Reading the result",
        "",
        _reading(drifted, [s for _, s in drift_t], drift_rep),
        "",
    ])
    return text, stable_rep, drift_rep, rep
