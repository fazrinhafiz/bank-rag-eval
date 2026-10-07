"""Pure metric functions - no I/O, so each one is unit-tested directly."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..ingest import split_sentences
from ..text import tokenize

_CITATION_TAG = re.compile(r"\s*\[[^\]]*#[^\]]*\]")


# --- retrieval ------------------------------------------------------------------------------------------
def first_rank(retrieved_docs: list[str], expected_doc: str) -> int | None:
    for rank, doc in enumerate(retrieved_docs, start=1):
        if doc == expected_doc:
            return rank
    return None


def hit_at_k(retrieved_docs: list[str], expected_doc: str, k: int) -> bool:
    rank = first_rank(retrieved_docs, expected_doc)
    return rank is not None and rank <= k


def reciprocal_rank(retrieved_docs: list[str], expected_doc: str) -> float:
    rank = first_rank(retrieved_docs, expected_doc)
    return 1.0 / rank if rank else 0.0


# --- answers --------------------------------------------------------------------------------------------
def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def missing_key_facts(answer: str, key_facts: list[str]) -> list[str]:
    a = _norm(answer)
    return [f for f in key_facts if _norm(f) not in a]


def key_fact_recall(answer: str, key_facts: list[str]) -> float:
    if not key_facts:
        return 1.0
    return 1 - len(missing_key_facts(answer, key_facts)) / len(key_facts)


def answer_sentences(answer: str) -> list[str]:
    return [s for s in split_sentences(_CITATION_TAG.sub("", answer)) if tokenize(s)]


def sentence_support(sentence: str, contexts: list[str]) -> float:
    """Share of the sentence's content tokens (numbers included) found in the single best context chunk."""
    tokens = set(tokenize(sentence))
    if not tokens or not contexts:
        return 0.0
    return max(len(tokens & set(tokenize(c))) / len(tokens) for c in contexts)


def faithfulness(answer: str, contexts: list[str], min_support: float = 0.8) -> float:
    """Lexical faithfulness: share of answer sentences supported by at least one retrieved chunk.
    Cheap and deterministic; trivially high for an extractive generator, informative for an LLM.
    The optional LLM-as-judge (eval/judge.py) is the semantic version."""
    sentences = answer_sentences(answer)
    if not sentences:
        return 0.0
    return sum(sentence_support(s, contexts) >= min_support for s in sentences) / len(sentences)


# --- classification -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PRF:
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int


def precision_recall_f1(y_true: list[bool], y_pred: list[bool]) -> PRF:
    tp = sum(t and p for t, p in zip(y_true, y_pred))
    fp = sum((not t) and p for t, p in zip(y_true, y_pred))
    fn = sum(t and (not p) for t, p in zip(y_true, y_pred))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return PRF(precision, recall, f1, tp, fp, fn)


def auroc(scores: list[float], labels: list[bool]) -> float | None:
    """P(score of a random positive > score of a random negative); ties count half. None if one class is empty."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = (len(s) - 1) * q / 100
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


# --- quality gates ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class GateResult:
    metric: str
    value: float | None
    op: str          # ">=" or "<="
    threshold: float
    passed: bool
    why: str = ""


def check_gates(metrics: dict[str, float], gates: dict[str, dict]) -> list[GateResult]:
    """gates: {metric: {min: x} | {max: y}, why: str}. A metric missing from the run fails its gate."""
    results = []
    for name, spec in gates.items():
        value = metrics.get(name)
        if "min" in spec:
            op, thr = ">=", float(spec["min"])
            ok = value is not None and value >= thr - 1e-9
        elif "max" in spec:
            op, thr = "<=", float(spec["max"])
            ok = value is not None and value <= thr + 1e-9
        else:
            raise ValueError(f"gate {name!r} needs 'min' or 'max'")
        results.append(GateResult(name, value, op, thr, ok, spec.get("why", "")))
    return results
