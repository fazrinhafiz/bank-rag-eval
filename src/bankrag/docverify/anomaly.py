"""Anomaly score for document figures, fitted on genuine DEV documents only.

Rules catch documents that contradict themselves. A careful forger keeps the arithmetic consistent; what is
harder to fake is how figures relate to each other (tax relative to income, how much the salary credit
moves a balance). The detector learns the normal range from genuine documents and never sees a tampered
one. A high score sends the document to a human; it never rejects on its own.

Two detectors are compared on the dev split and one is selected per document type (`bankrag doc-calibrate`):
    iforest   IsolationForest over all features (multivariate, but weak when ONE feature is far out,
              because random axis splits rarely pick that feature)
    robust_z  max over features of |x - median| / (1.4826 * MAD) (simple, explainable: the reviewer is told
              which figure is unusual)
Selection: recall on dev amount-changing tampers at the calibrated threshold, ties broken by AUROC.

The model file (data/docverify/anomaly_model.json) holds the training matrix, not a pickle: it is reviewable,
diffable and safe to load, and refitting with a fixed seed is deterministic.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest

from ..eval.metrics import auroc
from . import statutory

FEATURES = {
    "payslip": ["log_gross", "pcb_vs_schedule", "net_ratio", "allowance_ratio"],
    "bank_statement": ["log_opening", "log_credits", "log_debits", "credit_debit_log_ratio", "closing_vs_opening",
                       "n_transactions", "largest_credit_share"],
}
METHODS = ("robust_z", "iforest")
SEED = 0
N_TREES = 300


def features(doc_type: str, f: dict) -> list[float] | None:
    try:
        if doc_type == "payslip":
            g = f["gross_pay"]
            if not g:
                return None
            # Tax relative to what the simplified schedule gives for this income. Individual reliefs move it
            # a little below; a forger who inflates income but not tax moves it far below. +RM10 keeps
            # zero-tax incomes near 0.
            expected = statutory.pcb(g, statutory.epf(g))
            return [math.log(g), math.log((f["pcb"] + 10) / (expected + 10)), f["net_pay"] / g, (f["allowances"] or 0) / g]
        rows = f["transactions"]
        credits = sum(r["credit"] or 0 for r in rows)
        debits = sum(r["debit"] or 0 for r in rows)
        largest = max((r["credit"] or 0 for r in rows), default=0)
        return [math.log1p(f["opening_balance"]), math.log1p(credits), math.log1p(debits), math.log1p(credits) - math.log1p(debits),
                math.log1p(f["closing_balance"]) - math.log1p(f["opening_balance"]), float(len(rows)),
                largest / credits if credits else 0.0]
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


class _Detector:
    def __init__(self, method: str, X: np.ndarray):
        self.method = method
        if method == "iforest":
            self.forest = IsolationForest(n_estimators=N_TREES, random_state=SEED).fit(X)
        elif method == "robust_z":
            self.median = np.median(X, axis=0)
            self.scale = np.median(np.abs(X - self.median), axis=0) * 1.4826 + 1e-6
        else:
            raise ValueError(f"unknown anomaly method {method!r}")

    def scores(self, X: np.ndarray) -> np.ndarray:
        """Higher = more anomalous."""
        if self.method == "iforest":
            return -self.forest.score_samples(X)
        return np.abs((X - self.median) / self.scale).max(axis=1)

    def explain(self, x: list[float], names: list[str]) -> str | None:
        if self.method != "robust_z":
            return None
        z = np.abs((np.array(x) - self.median) / self.scale)
        return f"{names[int(z.argmax())]} is {z.max():.1f} robust SDs from genuine documents"


def _oof(method: str, X: np.ndarray) -> np.ndarray:
    """2-fold out-of-fold scores, so no genuine document is scored by a model that saw it."""
    idx, out = np.arange(len(X)), np.zeros(len(X))
    for fold in (0, 1):
        train, held = idx[idx % 2 != fold], idx[idx % 2 == fold]
        out[held] = _Detector(method, X[train]).scores(X[held])
    return out


def fit(dev_genuine: dict[str, list], dev_tampered: dict[str, list], quantile: float, source: str) -> dict:
    """Per document type: compare methods on dev, keep the best, fit it on all genuine dev documents."""
    out = {"version": 2, "trained_on": source, "quantile": quantile, "seed": SEED, "n_trees": N_TREES, "models": {}}
    for doc_type, rows in dev_genuine.items():
        X = np.array(rows, dtype=float)
        T = np.array(dev_tampered.get(doc_type) or np.empty((0, X.shape[1])), dtype=float)
        comparison = []
        for method in METHODS:
            oof = _oof(method, X)
            thr = float(np.quantile(oof, quantile))
            t_scores = _Detector(method, X).scores(T) if len(T) else np.array([])
            comparison.append({"method": method, "threshold": round(thr, 6),
                               "dev_genuine_flag_rate": round(float(np.mean(oof > thr)), 4),
                               "dev_tamper_recall": round(float(np.mean(t_scores > thr)), 4) if len(T) else None,
                               "dev_auroc": None if not len(T) else round(float(auroc(list(oof) + list(t_scores), [False] * len(oof) + [True] * len(T))), 4)})
        best = max(comparison, key=lambda c: (c["dev_tamper_recall"] or 0, c["dev_auroc"] or 0, c["method"] == "robust_z"))
        out["models"][doc_type] = {"method": best["method"], "features": FEATURES[doc_type], "n_train": len(X),
                                   "n_dev_tampered": len(T), "threshold": best["threshold"], "comparison": comparison,
                                   "X": [[round(v, 6) for v in r] for r in rows]}
    return out


class AnomalyModel:
    def __init__(self, spec: dict):
        self.spec = spec
        self.models = {t: (_Detector(m["method"], np.array(m["X"], dtype=float)), m["threshold"]) for t, m in spec["models"].items()}

    @classmethod
    def load(cls, path: str | Path) -> "AnomalyModel | None":
        p = Path(path)
        return cls(json.loads(p.read_text())) if p.exists() else None

    def score(self, doc_type: str, fields: dict) -> tuple[float | None, float | None, str | None]:
        """(score, threshold, explanation); Nones if the type has no model or features are missing."""
        if doc_type not in self.models:
            return None, None, None
        x = features(doc_type, fields)
        if x is None:
            return None, None, None
        det, thr = self.models[doc_type]
        return round(float(det.scores(np.array([x]))[0]), 6), thr, det.explain(x, FEATURES[doc_type])
