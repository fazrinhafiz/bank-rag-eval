import pytest

from bankrag.eval import metrics as M
from bankrag.eval.calibrate import calibrate, sweep


def test_retrieval_metrics():
    docs = ["fees", "credit-card", "credit-card"]
    assert not M.hit_at_k(docs, "credit-card", 1) and M.hit_at_k(docs, "credit-card", 3)
    assert M.reciprocal_rank(docs, "credit-card") == 0.5
    assert M.reciprocal_rank(docs, "privacy") == 0.0


def test_key_fact_recall_is_case_and_whitespace_insensitive():
    answer = "A charge of 1% applies, minimum rm10  and maximum RM100."
    assert M.key_fact_recall(answer, ["1%", "RM10", "RM100"]) == 1.0
    assert M.missing_key_facts(answer, ["RM50", "1%"]) == ["RM50"]
    assert M.key_fact_recall(answer, ["RM50", "1%"]) == 0.5


def test_faithfulness_counts_supported_sentences():
    ctx = ["The annual fee is RM250. It is waived after 12 retail transactions."]
    assert M.faithfulness("The annual fee is RM250. [credit-card#annual-fee]", ctx) == 1.0
    half = "The annual fee is RM250. [credit-card#annual-fee] Gold cardholders get free lounge access."
    assert M.faithfulness(half, ctx) == 0.5
    assert M.faithfulness("", ctx) == 0.0


def test_precision_recall_f1():
    prf = M.precision_recall_f1([True, True, False, False], [True, False, True, False])
    assert (prf.precision, prf.recall, prf.f1) == (0.5, 0.5, 0.5)
    assert M.precision_recall_f1([True], [False]).f1 == 0.0


def test_auroc():
    assert M.auroc([0.9, 0.8, 0.1], [True, True, False]) == 1.0
    assert M.auroc([0.5, 0.5], [True, False]) == 0.5
    assert M.auroc([0.5], [True]) is None


def test_gates_pass_fail_and_missing_metric():
    gates = {"pii_leak_rate": {"max": 0.0}, "hit_at_3": {"min": 0.9}, "judge_faithfulness": {"min": 0.8}}
    res = {g.metric: g.passed for g in M.check_gates({"pii_leak_rate": 0.0, "hit_at_3": 0.89}, gates)}
    assert res == {"pii_leak_rate": True, "hit_at_3": False, "judge_faithfulness": False}
    assert all(g.passed for g in M.check_gates({"pii_leak_rate": 0.0, "hit_at_3": 0.9, "judge_faithfulness": 0.8}, gates))
    assert not M.check_gates({"pii_leak_rate": 0.01}, {"pii_leak_rate": {"max": 0.0}})[0].passed
    with pytest.raises(ValueError):
        M.check_gates({}, {"x": {"target": 1}})


def test_sweep_puts_threshold_in_the_gap_between_classes():
    scores = [("u1", 0.1, True), ("u2", 0.2, True), ("a1", 0.6, False), ("a2", 0.8, False)]
    best = max(sweep(scores), key=lambda r: r.f1)
    assert best.f1 == 1.0 and 0.2 < best.threshold < 0.6


def test_calibration_refuses_test_split_items():
    items = [{"id": "x", "split": "test", "category": "answerable", "question": "q", "expect_abstain": False}]
    with pytest.raises(ValueError, match="dev split only"):
        calibrate(items, retriever=None, top_k=3)
