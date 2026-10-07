"""Abstain-threshold calibration on the DEV split only.

The test split is never read here, so the quality gate (run on test) measures a threshold it did not
influence. `calibrate()` takes the items it is given and refuses anything that is not dev.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..guardrails import check_input
from ..retrieval import Retriever
from .metrics import precision_recall_f1


@dataclass
class SweepRow:
    threshold: float
    precision: float
    recall: float
    f1: float
    answer_rate: float   # share of answerable items that would still be answered


@dataclass
class Calibration:
    threshold: float
    best: SweepRow
    rows: list[SweepRow]
    n_items: int
    n_should_abstain: int
    scores: list[tuple[str, float, bool]]   # (id, confidence, expect_abstain)


def confidences(items: list[dict], retriever: Retriever, top_k: int) -> list[tuple[str, float, bool]]:
    """Confidence the pipeline would see: PII-masked query, injection items excluded (they never reach retrieval)."""
    out = []
    for item in items:
        if item["category"] == "injection":
            continue
        masked = check_input(item["question"]).masked_text
        out.append((item["id"], retriever.search(masked, top_k).confidence, item["expect_abstain"]))
    return out


def sweep(scores: list[tuple[str, float, bool]]) -> list[SweepRow]:
    """Candidate thresholds are midpoints between consecutive distinct confidences, so the chosen one
    sits in the gap between classes rather than exactly on a dev item's score."""
    values = sorted({round(s, 6) for _, s, _ in scores})
    candidates = [0.0] + [(a + b) / 2 for a, b in zip(values, values[1:])] + [values[-1] + 1e-3]
    y_true = [abstain for _, _, abstain in scores]
    n_answerable = sum(not y for y in y_true)
    rows = []
    for t in candidates:
        y_pred = [s < t for _, s, _ in scores]
        prf = precision_recall_f1(y_true, y_pred)
        answered = sum((not y) and (not p) for y, p in zip(y_true, y_pred))
        rows.append(SweepRow(round(t, 4), prf.precision, prf.recall, prf.f1, answered / n_answerable if n_answerable else 0.0))
    return rows


def calibrate(items: list[dict], retriever: Retriever, top_k: int) -> Calibration:
    leaked = [i["id"] for i in items if i["split"] != "dev"]
    if leaked:
        raise ValueError(f"calibration must use the dev split only; got non-dev items {leaked[:5]}")
    scores = confidences(items, retriever, top_k)
    rows = sweep(scores)
    best_f1 = max(r.f1 for r in rows)
    tied = [r for r in rows if abs(r.f1 - best_f1) < 1e-9]
    best = tied[len(tied) // 2]   # middle of the tied range: the most margin on both sides
    return Calibration(best.threshold, best, rows, len(scores), sum(a for _, _, a in scores), scores)
