"""Validates the golden set itself. A wrong label silently corrupts every metric, so the labels get tests too."""
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "eval" / "golden_set.jsonl"
DOCS = ROOT / "data" / "docs"
CATEGORIES = {"answerable", "unanswerable", "injection", "pii"}
REQUIRED = {"id", "question", "category", "split", "expected_doc", "key_facts", "expect_abstain"}


def _items():
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def _doc_text(doc_id):
    return (DOCS / f"{doc_id}.md").read_text()


def test_schema_and_unique_ids():
    items = _items()
    assert len({i["id"] for i in items}) == len(items), "duplicate ids"
    for item in items:
        assert REQUIRED <= item.keys(), f"{item['id']} missing fields"
        assert item["category"] in CATEGORIES
        assert item["split"] in {"dev", "test"}


@pytest.mark.parametrize("item", [i for i in _items() if i["category"] in {"answerable", "pii"}], ids=lambda i: i["id"])
def test_key_facts_appear_verbatim_in_expected_doc(item):
    text = _doc_text(item["expected_doc"])
    assert item["key_facts"], f"{item['id']} has no key facts"
    for fact in item["key_facts"]:
        assert fact.lower() in text.lower(), f"{item['id']}: '{fact}' not in {item['expected_doc']}.md"
    assert item["expect_abstain"] is False


def test_negative_items_expect_abstain_and_have_no_doc():
    for item in _items():
        if item["category"] in {"unanswerable", "injection"}:
            assert item["expect_abstain"] is True
            assert item["expected_doc"] is None and item["key_facts"] == []


def test_pii_items_contain_their_pii_values():
    for item in (i for i in _items() if i["category"] == "pii"):
        assert item["pii_values"], item["id"]
        for value in item["pii_values"]:
            assert value in item["question"], f"{item['id']}: {value} not in question"


def test_split_is_roughly_40_60_and_every_category_is_in_both_splits():
    items = _items()
    dev_share = sum(i["split"] == "dev" for i in items) / len(items)
    assert 0.35 <= dev_share <= 0.45
    per_split = Counter((i["category"], i["split"]) for i in items)
    for cat in CATEGORIES:
        assert per_split[(cat, "dev")] > 0 and per_split[(cat, "test")] > 0, cat


def test_answerable_items_cover_all_docs():
    covered = {i["expected_doc"] for i in _items() if i["category"] == "answerable"}
    assert covered == {p.stem for p in DOCS.glob("*.md")}
