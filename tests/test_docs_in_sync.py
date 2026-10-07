"""The docs may only quote numbers that the committed reports contain."""
from pathlib import Path

from bankrag.eval.doc_blocks import DOCS, sync

ROOT = Path(__file__).resolve().parents[1]


def test_generated_result_blocks_match_committed_reports():
    assert sync(ROOT, check=True) == [], "run: python scripts/update_doc_results.py"


def test_docs_contain_the_generated_blocks():
    for rel in DOCS:
        assert "<!-- generated:results:start -->" in (ROOT / rel).read_text(), rel
