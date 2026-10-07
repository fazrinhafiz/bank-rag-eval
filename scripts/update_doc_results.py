"""Regenerate the result tables in README.md and docs/MODEL_CARD.md from reports/*.json.

    python scripts/update_doc_results.py          # rewrite the generated blocks
    python scripts/update_doc_results.py --check  # exit 1 if docs and reports disagree
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bankrag.eval.doc_blocks import sync  # noqa: E402

if __name__ == "__main__":
    check = "--check" in sys.argv
    stale = sync(ROOT, check=check)
    if check and stale:
        print(f"stale generated results in: {', '.join(stale)} - run python scripts/update_doc_results.py")
        sys.exit(1)
    print("updated: " + (", ".join(stale) if stale else "nothing (already in sync)"))
