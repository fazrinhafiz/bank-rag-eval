import shutil
from pathlib import Path

from bankrag.eval.harness import source_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def _copy(tmp_path):
    for rel in ("src", "config.yaml", "prompts", "eval", "data"):
        src, dst = ROOT / rel, tmp_path / rel
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info")) if src.is_dir() else shutil.copy(src, dst)
    return tmp_path


def test_fingerprint_is_stable_and_ignores_reports(tmp_path):
    root = _copy(tmp_path)
    before = source_fingerprint(root)
    (root / "reports").mkdir()
    (root / "reports" / "eval_report.md").write_text("regenerated")
    assert source_fingerprint(root) == before == source_fingerprint(ROOT)


def test_fingerprint_changes_when_code_config_or_docs_change(tmp_path):
    root = _copy(tmp_path)
    base = source_fingerprint(root)
    for rel in ("config.yaml", "src/bankrag/pipeline.py", "data/docs/credit-card.md", "eval/thresholds.yaml"):
        path = root / rel
        original = path.read_text()
        path.write_text(original + "\n# change\n")
        assert source_fingerprint(root) != base, rel
        path.write_text(original)
    assert source_fingerprint(root) == base
