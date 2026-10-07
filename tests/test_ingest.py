from pathlib import Path

import pytest

from bankrag.ingest import load_corpus, parse_document, slugify

DOCS = Path(__file__).resolve().parents[1] / "data" / "docs"

SAMPLE = """---
doc_id: demo
title: Demo Terms
---
> FICTIONAL DOCUMENT.

# Demo Terms

## Annual fee
The fee is RM250. It is waived after 12 transactions.

## Late Payment / Charges
A charge of 1% applies.
"""


def test_one_chunk_per_section_with_doc_and_slug_ids():
    chunks = parse_document(SAMPLE)
    assert [c.id for c in chunks] == ["demo#annual-fee", "demo#late-payment-charges"]
    assert chunks[0].text == "The fee is RM250. It is waived after 12 transactions."
    assert chunks[0].title == "Demo Terms" and chunks[0].section == "Annual fee"


def test_text_before_first_section_is_dropped():
    assert all("FICTIONAL" not in c.text for c in parse_document(SAMPLE))


def test_long_section_split_into_overlapping_windows():
    sentences = " ".join(f"Sentence number {i} has five words." for i in range(30))  # 6 words each
    doc = f"---\ndoc_id: long\n---\n## Big section\n{sentences}\n"
    chunks = parse_document(doc, max_words=40, overlap_words=12)
    assert len(chunks) > 1
    assert [c.id for c in chunks][:2] == ["long#big-section-1", "long#big-section-2"]
    assert all(len(c.text.split()) <= 40 for c in chunks)
    tail = " ".join(chunks[0].text.split()[-12:])   # overlap_words=12 -> last two 6-word sentences
    assert chunks[1].text.startswith(tail)            # tail of window 1 opens window 2
    joined = " ".join(c.text for c in chunks)
    assert all(f"number {i} " in joined for i in range(30))  # nothing lost


def test_missing_front_matter_rejected():
    with pytest.raises(ValueError):
        parse_document("## Section\ntext")


def test_slugify():
    assert slugify("Lost or stolen card") == "lost-or-stolen-card"
    assert slugify("Fees & charges (2026)") == "fees-charges-2026"


def test_real_corpus_chunk_ids_are_unique_and_match_headings():
    chunks = load_corpus(DOCS)
    assert len(chunks) == len({c.id for c in chunks})
    assert {c.doc_id for c in chunks} == {p.stem for p in DOCS.glob("*.md")}
    assert "credit-card#late-payment-charge" in {c.id for c in chunks}
