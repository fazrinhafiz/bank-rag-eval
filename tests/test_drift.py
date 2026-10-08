import numpy as np
import pytest

from bankrag.monitoring.drift import ks_2samp, monitor, psi_band, psi_categorical, psi_continuous


def test_psi_of_identical_distributions_is_zero():
    x = list(np.random.default_rng(0).normal(size=2000))
    assert psi_continuous(x, x) == pytest.approx(0, abs=1e-9)


def test_psi_hand_computed_categorical():
    # baseline 50/50, current 80/20: (0.8-0.5)ln(0.8/0.5) + (0.2-0.5)ln(0.2/0.5)
    value, detail = psi_categorical(["a"] * 50 + ["b"] * 50, ["a"] * 80 + ["b"] * 20)
    assert value == pytest.approx(0.3 * np.log(1.6) + (-0.3) * np.log(0.4))
    assert detail == {"a": (0.5, 0.8), "b": (0.5, 0.2)}


def test_psi_detects_mean_shift_and_is_small_for_resample():
    rng = np.random.default_rng(1)
    base = rng.normal(0, 1, 5000)
    assert psi_continuous(base, rng.normal(0, 1, 5000)) < 0.10
    assert psi_continuous(base, rng.normal(1, 1, 5000)) > 0.25


def test_psi_handles_new_category_without_infinity():
    value, _ = psi_categorical(["a"] * 10, ["a"] * 5 + ["new"] * 5)
    assert np.isfinite(value) and value > 0.25


@pytest.mark.parametrize("value,band", [(0.0, "stable"), (0.099, "stable"), (0.10, "moderate"),
                                        (0.25, "moderate"), (0.2501, "significant")])
def test_psi_bands(value, band):
    assert psi_band(value) == band


def test_ks_identical_and_shifted():
    rng = np.random.default_rng(2)
    a = rng.normal(size=500)
    d, p = ks_2samp(a, a)
    assert d == 0 and p == 1.0
    d, p = ks_2samp(a, rng.normal(0.5, 1, 500))
    assert d > 0.1 and p < 0.001


def test_ks_matches_scipy_when_available():
    stats = pytest.importorskip("scipy.stats")
    rng = np.random.default_rng(3)
    a, b = rng.normal(size=300), rng.normal(0.2, 1, 400)
    d, p = ks_2samp(a, b)
    ref = stats.ks_2samp(a, b, method="asymp")
    assert d == pytest.approx(ref.statistic)
    assert p == pytest.approx(ref.pvalue, rel=0.05, abs=1e-4)


def _trace(score, top_doc="credit-card", abstained=False, n=6):
    return {"top_score": score, "query_len": n, "top_doc": top_doc, "abstained": abstained, "blocked": False, "pii_types": []}


def test_monitor_flags_abstention_surge_and_stays_quiet_on_same_traffic():
    base = [_trace(0.6 + 0.001 * i) for i in range(200)]
    assert monitor(base, base).status == "OK"
    drifted = base[:100] + [_trace(0.1, "personal-loan", abstained=True, n=9) for _ in range(100)]
    rep = monitor(base, drifted)
    assert rep.status == "ALERT"
    assert any(a.startswith("abstained_rate") for a in rep.alerts)


def test_query_term_drift_catches_new_subject_but_not_resample():
    from bankrag.monitoring.drift import term_psi

    def t(q):
        return {**_trace(0.5), "masked_query": q}

    base = [t(q) for q in ["what is the annual fee", "how do I report fraud", "late payment charge"] * 60]
    same = [t(q) for q in ["late payment charge", "what is the annual fee", "how do I report fraud"] * 60]
    surge = base[:90] + [t("why did my top up fail") for _ in range(90)]
    assert term_psi(base, same)[0] < 0.10
    value, movers = term_psi(base, surge)
    assert value > 0.25 and "top" in movers


def test_query_terms_never_counts_pii_mask_placeholders():
    from bankrag.monitoring.drift import query_terms

    # the placeholders vanish; the real words "phone" and "email" are kept (stemmed)
    assert query_terms([{"masked_query": "My IC is [NRIC] and phone [PHONE], email [EMAIL]"}]) == ["ic", "phon", "email"]
