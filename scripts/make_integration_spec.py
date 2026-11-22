"""Regenerate docs/INTEGRATION_SPEC.md and docs/openapi.json from the API (see bankrag.integration_spec).

    python scripts/make_integration_spec.py          # rewrite
    python scripts/make_integration_spec.py --check  # exit 1 if out of date
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bankrag.integration_spec import sync  # noqa: E402

if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    check = "--check" in sys.argv
    stale = sync(ROOT, check=check)
    if check and stale:
        print(f"out of date: {', '.join(stale)} - run python scripts/make_integration_spec.py")
        sys.exit(1)
    print("updated: " + (", ".join(stale) if stale else "nothing (already in sync)"))
