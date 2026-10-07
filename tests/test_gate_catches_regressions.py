"""Mutation tests for the quality gate: break one thing on purpose and check the gate notices.

A gate that passes a broken system is worse than no gate. Each mutant below must fail at least the
gate that is responsible for catching it, and the unmodified system must pass every gate.
"""
from pathlib import Path

import pytest
import yaml

import bankrag.pipeline as pipeline_module
from bankrag.config import load_config, with_overrides
from bankrag.eval.harness import run_eval
from bankrag.eval.metrics import check_gates
from bankrag.guardrails import InputCheck, detect_injection
from bankrag.pipeline import Pipeline
from bankrag.retrieval import RetrievalResult

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def gates():
    return yaml.safe_load((ROOT / "eval" / "thresholds.yaml").read_text())["gates"]


def failed_gates(cfg, gates, pipe=None):
    run = run_eval(cfg, "test", pipeline=pipe or Pipeline(cfg))
    return {g.metric for g in check_gates(run.metrics, gates) if not g.passed}


def test_unmodified_system_passes_every_gate(cfg, gates):
    assert failed_gates(cfg, gates) == set()


def test_gate_catches_disabled_pii_masking(cfg, gates, monkeypatch):
    monkeypatch.setattr(pipeline_module, "check_input", lambda t: InputCheck(t, [], detect_injection(t)))
    assert {"pii_mask_rate", "pii_leak_rate"} <= failed_gates(cfg, gates)


def test_gate_catches_disabled_injection_detection(cfg, gates, monkeypatch):
    real = pipeline_module.check_input
    monkeypatch.setattr(pipeline_module, "check_input", lambda t: InputCheck(real(t).masked_text, real(t).pii, []))
    assert "injection_block_rate" in failed_gates(cfg, gates)


def test_gate_catches_removed_abstain_threshold(cfg, gates):
    assert "abstention_recall" in failed_gates(with_overrides(cfg, abstain_threshold=0.0), gates)


def test_gate_catches_broken_retrieval_ranking(cfg, gates):
    pipe = Pipeline(cfg)
    original = pipe.retriever.search

    def worst_first(query, top_k=3):
        res = original(query, top_k=len(pipe.chunks))
        return RetrievalResult(res.mode, list(reversed(res.hits))[:top_k], res.confidence)

    pipe.retriever.search = worst_first
    assert {"hit_at_3", "mrr"} <= failed_gates(cfg, gates, pipe)


def test_gate_catches_overzealous_injection_filter(cfg, gates, monkeypatch):
    real = pipeline_module.check_input
    monkeypatch.setattr(pipeline_module, "check_input",
                        lambda t: InputCheck(real(t).masked_text, real(t).pii, real(t).injection_rules or (["fee"] if "fee" in t.lower() else [])))
    assert "false_block_rate" in failed_gates(cfg, gates)
