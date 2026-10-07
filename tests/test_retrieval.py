from pathlib import Path

import pytest

from bankrag.ingest import Chunk, load_corpus
from bankrag.retrieval import BM25, Retriever, rrf

DOCS = Path(__file__).resolve().parents[1] / "data" / "docs"


def test_bm25_ranks_document_with_query_terms_first():
    docs = [["savings", "interest", "rate"], ["card", "annual", "fee"], ["loan", "profit", "rate"]]
    scores = BM25(docs).scores(["annual", "fee"])
    assert scores.argmax() == 1 and scores[0] == scores[2] == 0


def test_bm25_rare_terms_weigh_more_than_common_terms():
    bm = BM25([["rate", "card"], ["rate", "loan"], ["rate", "fee"]])
    assert bm.idf["card"] > bm.idf["rate"]


def test_bm25_term_frequency_saturates():
    bm = BM25([["fee"], ["fee"] * 2, ["fee"] * 20, ["other"]], k1=1.5, b=0.0)
    s = bm.scores(["fee"])
    assert s[0] < s[1] < s[2] and (s[2] - s[1]) < (s[1] - s[0]) * 5
    assert s[2] < bm.idf["fee"] * (1.5 + 1)   # bounded by idf * (k1 + 1)


def test_bm25_unknown_terms_score_zero():
    assert BM25([["a", "b"]]).scores(["zzz"]).sum() == 0


def test_rrf_maths():
    fused = rrf([[0, 1, 2], [1, 0, 2]], k=60)
    assert fused[0] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[0] == pytest.approx(fused[1])
    assert fused[2] == pytest.approx(2 / 63)
    assert rrf([[5, 6], [5, 7]])[5] > rrf([[5, 6], [5, 7]])[6]   # agreement at rank 1 wins


@pytest.fixture(scope="module")
def chunks():
    return load_corpus(DOCS)


@pytest.mark.parametrize("mode", ["bm25", "tfidf", "hybrid"])
def test_every_mode_finds_a_direct_question(chunks, mode):
    res = Retriever(chunks, mode).search("What is the late payment charge on the credit card?", 3)
    assert res.hits[0].chunk.id == "credit-card#late-payment-charge"
    assert 0 < res.confidence <= 1


@pytest.mark.parametrize("mode", ["bm25", "tfidf", "hybrid"])
def test_off_topic_query_gets_lower_confidence_than_on_topic(chunks, mode):
    r = Retriever(chunks, mode)
    assert r.search("Who won the FIFA World Cup?").confidence < r.search("What is the annual fee for the credit card?").confidence


def test_unknown_mode_rejected(chunks):
    with pytest.raises(ValueError):
        Retriever(chunks, "embeddings")


def test_ranking_is_deterministic(chunks):
    r = Retriever(chunks, "hybrid")
    a = [h.chunk.id for h in r.search("cash advance fee", 5).hits]
    assert a == [h.chunk.id for h in r.search("cash advance fee", 5).hits]


def test_empty_query_has_zero_confidence():
    r = Retriever([Chunk("d#s", "d", "T", "S", "some text")], "bm25")
    assert r.search("the and of").confidence == 0.0
