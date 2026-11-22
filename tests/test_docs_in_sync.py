"""The docs may only quote numbers that the committed reports contain."""
from pathlib import Path

from bankrag.eval.doc_blocks import DOCS, sync

ROOT = Path(__file__).resolve().parents[1]


def test_generated_result_blocks_match_committed_reports():
    assert sync(ROOT, check=True) == [], "run: python scripts/update_doc_results.py"


def test_docs_contain_the_generated_blocks():
    for rel in DOCS:
        assert "<!-- generated:results:start -->" in (ROOT / rel).read_text(), rel


def test_relative_links_in_docs_resolve():
    """Every relative markdown link in the docs points at a file that exists (generated blocks included)."""
    import re
    link = re.compile(r"\]\((?!https?:|#|mailto:)([^)#\s]+)(?:#[^)]*)?\)")
    files = ["README.md", *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md")),
             "infra/azure/README.md", "eval/README.md"]
    broken = [f"{rel}: {target}" for rel in files for target in link.findall((ROOT / rel).read_text())
              if not ((ROOT / rel).parent / target).exists()]
    assert not broken, broken
