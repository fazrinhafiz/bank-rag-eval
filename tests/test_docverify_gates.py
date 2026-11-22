"""Mutation tests for the document gates: switching off a check must fail the gate that guards it.

The statutory check is the one group with no gate here: on this corpus every tamper it would catch is
already caught by the arithmetic check, so disabling it changes nothing measurable (it is unit-tested).
"""
import pytest

pytest.importorskip("pypdf")
pytest.importorskip("fpdf")

import yaml  # noqa: E402

from bankrag.config import load_config  # noqa: E402
from bankrag.docverify.evaluate import run  # noqa: E402
from bankrag.eval.metrics import check_gates  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def gates():
    return yaml.safe_load(open("eval/docverify_thresholds.yaml"))["gates"]


def _failed(cfg, gates, disabled=frozenset(), anomaly=True):
    return {g.metric for g in check_gates(run(cfg, "test", disabled=set(disabled), anomaly=anomaly)["metrics"], gates) if not g.passed}


def test_gates_pass_with_every_check_on(cfg, gates):
    assert _failed(cfg, gates) == set()


@pytest.mark.parametrize("group,gate", [
    ("arithmetic", "recall_arithmetic"),
    ("identity", "recall_name_mismatch"),
    ("identity", "recall_invalid_nric"),
    ("dates", "recall_period_mismatch"),
    ("registry", "recall_duplicate_resubmission"),
    ("fonts", "recall_font_inconsistency"),
    ("metadata", "recall_metadata_modified"),
    ("anomaly", "recall_consistent_forgery"),
])
def test_disabling_a_check_fails_its_gate(cfg, gates, group, gate):
    assert gate in _failed(cfg, gates, {group})
