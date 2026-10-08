"""Drift monitoring over traces: PSI and a two-sample KS test, baseline window vs current window.

PSI bands (the convention used for credit-scorecard monitoring in banking):
    < 0.10 stable · 0.10-0.25 moderate shift (investigate) · > 0.25 significant shift (act)
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from ..text import tokenize

TOP_TERMS = 30
# PII mask placeholders are removed before counting terms. [CARD] is kept: it reads as the real word "card".
_MASKS = re.compile(r"\[(NRIC|PHONE|EMAIL)\]")

EPS = 1e-4


def psi_band(value: float) -> str:
    return "stable" if value < 0.10 else "moderate" if value <= 0.25 else "significant"


def _psi(expected: np.ndarray, actual: np.ndarray) -> float:
    e = np.clip(expected, EPS, None)
    a = np.clip(actual, EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_continuous(baseline: list[float], current: list[float], bins: int = 10) -> float:
    """Bins are baseline quantiles (equal-population), so each baseline bucket holds ~1/bins of the mass."""
    base, cur = np.asarray(baseline, float), np.asarray(current, float)
    edges = np.unique(np.quantile(base, np.linspace(0, 1, bins + 1)[1:-1]))
    edges = np.concatenate([[-np.inf], edges, [np.inf]])
    e = np.histogram(base, edges)[0] / len(base)
    a = np.histogram(cur, edges)[0] / len(cur)
    return _psi(e, a)


def psi_categorical(baseline: list[str], current: list[str]) -> tuple[float, dict[str, tuple[float, float]]]:
    cats = sorted(set(baseline) | set(current))
    b, c = Counter(baseline), Counter(current)
    e = np.array([b[k] / len(baseline) for k in cats])
    a = np.array([c[k] / len(current) for k in cats])
    return _psi(e, a), {k: (float(x), float(y)) for k, x, y in zip(cats, e, a)}


def ks_2samp(a: list[float], b: list[float]) -> tuple[float, float]:
    """Two-sample Kolmogorov-Smirnov statistic D and asymptotic p-value (no scipy needed)."""
    a, b = np.sort(np.asarray(a, float)), np.sort(np.asarray(b, float))
    grid = np.concatenate([a, b])
    d = float(np.max(np.abs(np.searchsorted(a, grid, "right") / len(a) - np.searchsorted(b, grid, "right") / len(b))))
    ne = len(a) * len(b) / (len(a) + len(b))
    lam = (math.sqrt(ne) + 0.12 + 0.11 / math.sqrt(ne)) * d
    if lam < 1e-3:
        return d, 1.0
    p = 2 * sum((-1) ** (k - 1) * math.exp(-2 * k * k * lam * lam) for k in range(1, 101))
    return d, float(min(max(p, 0.0), 1.0))


def query_terms(traces: list[dict]) -> list[str]:
    """Content terms of the PII-masked queries (stemmed, stopwords removed, mask placeholders dropped)."""
    out = []
    for t in traces:
        out += tokenize(_MASKS.sub(" ", t.get("masked_query", "")))
    return out


def term_weights(traces: list[dict]) -> Counter:
    """Each query contributes a total weight of 1, split evenly over its distinct terms, so a single long
    prompt (e.g. a 300-word jailbreak) cannot outweigh hundreds of short customer questions."""
    weights: Counter = Counter()
    for t in traces:
        terms = set(query_terms([t]))
        for w in terms:
            weights[w] += 1 / len(terms)
    return weights


def term_psi(baseline: list[dict], current: list[dict], top: int = TOP_TERMS) -> tuple[float, dict]:
    """Categorical PSI over what people ask: the union of each window's `top` heaviest terms, with
    everything else pooled as <other>. Catches a shift in subject even when the shifted traffic
    looks like the baseline on confidence, abstention and answered topic."""
    b, c = term_weights(baseline), term_weights(current)
    keep = {w for w, _ in b.most_common(top)} | {w for w, _ in c.most_common(top)}
    cats = sorted(keep) + ["<other>"]

    def dist(weights: Counter) -> np.ndarray:
        total = sum(weights.values()) or 1.0
        shares = [weights.get(k, 0.0) / total for k in cats[:-1]]
        return np.array(shares + [1 - sum(shares)])

    e, a = dist(b), dist(c)
    detail = {k: (float(x), float(y)) for k, x, y in zip(cats, e, a)}
    movers = sorted(detail.items(), key=lambda kv: -abs(kv[1][1] - kv[1][0]))[:6]
    return _psi(e, a), dict(movers)


def topic(trace: dict) -> str:
    if trace.get("blocked"):
        return "<blocked>"
    if trace.get("abstained"):
        return "<abstained>"
    return trace.get("top_doc") or "<none>"


@dataclass
class FeatureDrift:
    name: str
    psi: float
    band: str
    ks_d: float | None = None
    ks_p: float | None = None
    detail: dict = field(default_factory=dict)


@dataclass
class RateChange:
    name: str
    baseline: float
    current: float
    alert: bool

    @property
    def delta(self) -> float:
        return self.current - self.baseline


@dataclass
class MonitorReport:
    n_baseline: int
    n_current: int
    features: list[FeatureDrift]
    rates: list[RateChange]

    @property
    def alerts(self) -> list[str]:
        out = [f"{f.name}: PSI {f.psi:.3f} ({f.band})" for f in self.features if f.band != "stable"]
        out += [f"{r.name}: {r.baseline:.1%} -> {r.current:.1%} ({r.delta:+.1%})" for r in self.rates if r.alert]
        return out

    @property
    def status(self) -> str:
        if any(f.band == "significant" for f in self.features) or any(r.alert for r in self.rates):
            return "ALERT"
        if any(f.band == "moderate" for f in self.features):
            return "WATCH"
        return "OK"


RATE_ALERT_PP = 0.10   # absolute change in a behavioural rate that raises an alert


def monitor(baseline: list[dict], current: list[dict]) -> MonitorReport:
    if not baseline or not current:
        raise ValueError("both windows need at least one trace")
    feats = []
    # confidence only exists for requests that reached retrieval
    b_conf = [t["top_score"] for t in baseline if not t["blocked"]]
    c_conf = [t["top_score"] for t in current if not t["blocked"]]
    for name, b, c in (("retrieval_confidence", b_conf, c_conf),
                       ("query_length", [t["query_len"] for t in baseline], [t["query_len"] for t in current])):
        value = psi_continuous(b, c)
        d, p = ks_2samp(b, c)
        feats.append(FeatureDrift(name, value, psi_band(value), d, p))
    value, detail = psi_categorical([topic(t) for t in baseline], [topic(t) for t in current])
    feats.append(FeatureDrift("topic_mix", value, psi_band(value), detail=detail))
    if all("masked_query" in t for t in baseline + current):
        value, movers = term_psi(baseline, current)
        feats.append(FeatureDrift("query_terms", value, psi_band(value), detail=movers))

    rate = lambda ts, key: sum(bool(t[key]) for t in ts) / len(ts)
    rates = []
    for key in ("abstained", "blocked"):
        b, c = rate(baseline, key), rate(current, key)
        rates.append(RateChange(f"{key}_rate", b, c, abs(c - b) >= RATE_ALERT_PP))
    b = sum(bool(t["pii_types"]) for t in baseline) / len(baseline)
    c = sum(bool(t["pii_types"]) for t in current) / len(current)
    rates.append(RateChange("pii_rate", b, c, abs(c - b) >= RATE_ALERT_PP))
    return MonitorReport(len(baseline), len(current), feats, rates)


def report_markdown(rep: MonitorReport, title: str) -> str:
    lines = [f"## {title}", "", f"Baseline {rep.n_baseline} traces vs current {rep.n_current} traces. **Status: {rep.status}**", "",
             "| feature | PSI | band | KS D | KS p-value |", "|---|---|---|---|---|"]
    for f in rep.features:
        ks = f"| {f.ks_d:.3f} | {f.ks_p:.2g} |" if f.ks_d is not None else "| - | - |"
        lines.append(f"| {f.name} | {f.psi:.3f} | {f.band} {ks}")
    lines += ["", "| behavioural rate | baseline | current | change | alert (>= 10 pp) |", "|---|---|---|---|---|"]
    lines += [f"| {r.name} | {r.baseline:.1%} | {r.current:.1%} | {r.delta:+.1%} | {'**yes**' if r.alert else 'no'} |" for r in rep.rates]
    topic_mix = next(f for f in rep.features if f.name == "topic_mix")
    lines += ["", "Topic mix (share of traffic by answered top document; abstained/blocked shown separately):", "",
              "| topic | baseline | current |", "|---|---|---|"]
    lines += [f"| {k} | {b:.1%} | {c:.1%} |" for k, (b, c) in sorted(topic_mix.detail.items(), key=lambda kv: -kv[1][1])]
    terms = next((f for f in rep.features if f.name == "query_terms"), None)
    if terms:
        lines += ["", "Query terms that moved most (share of all content terms, stemmed):", "",
                  "| term | baseline | current |", "|---|---|---|"]
        lines += [f"| {k} | {b:.1%} | {c:.1%} |" for k, (b, c) in terms.detail.items()]
    if rep.alerts:
        lines += ["", "Alerts:", *[f"- {a}" for a in rep.alerts]]
    return "\n".join(lines) + "\n"
