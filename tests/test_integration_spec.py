"""docs/INTEGRATION_SPEC.md and docs/openapi.json must match the API the code serves."""
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pypdf")

from bankrag.integration_spec import sync  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_integration_spec_matches_the_code():
    assert sync(ROOT, check=True) == [], "run: python scripts/make_integration_spec.py"
