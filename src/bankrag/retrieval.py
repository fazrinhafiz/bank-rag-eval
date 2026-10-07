"""Retrieval: BM25 (from scratch), TF-IDF cosine (offline stand-in for embeddings) and RRF hybrid.

Each retriever returns hits plus a `confidence` in [0, 1] for the top hit. Confidence is a normalised
relevance score, not a probability; the abstain decision threshold on it is calibrated on the dev split
(`bankrag calibrate`), and the eval report shows how well it separates right from wrong retrievals.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .ingest import Chunk
from .text import content_text, tokenize

MODES = ("bm25", "tfidf", "hybrid")


class BM25:
    """Okapi BM25 over pre-tokenised documents."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = np.array([len(d) for d in docs], dtype=float)
        self.avgdl = float(self.len.mean()) if docs else 0.0
        self.n = len(docs)
        df = Counter(t for d in docs for t in set(d))
        self.idf = {t: self.idf_for(c) for t, c in df.items()}

    def idf_for(self, df: int) -> float:
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(self.n)
        norm = self.k1 * (1 - self.b + self.b * self.len / self.avgdl)
        for term in set(query):
            idf = self.idf.get(term)
            if idf is None:
                continue
            tf = np.array([d.get(term, 0) for d in self.tf], dtype=float)
            out += idf * tf * (self.k1 + 1) / (tf + norm)
        return out

    def max_score(self, query: list[str]) -> float:
        """Score of an average-length doc containing every query term once. Unseen terms count at
        maximum idf, so a query full of words the corpus never uses gets low confidence."""
        unseen = self.idf_for(0)
        return sum(self.idf.get(t, unseen) for t in set(query))


def rrf(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    """Reciprocal Rank Fusion: score(d) = sum_i 1 / (k + rank_i(d)), ranks are 1-based."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking, start=1):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank)
    return fused


@dataclass
class Hit:
    chunk: Chunk
    rank: int
    score: float          # mode-native score (BM25, cosine or RRF)
    bm25_conf: float
    tfidf_conf: float


@dataclass
class RetrievalResult:
    mode: str
    hits: list[Hit] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def top_doc(self) -> str | None:
        return self.hits[0].chunk.doc_id if self.hits else None


class Retriever:
    def __init__(self, chunks: list[Chunk], mode: str = "hybrid", k1: float = 1.5, b: float = 0.75, rrf_k: int = 60):
        if mode not in MODES:
            raise ValueError(f"unknown retrieval mode {mode!r}; expected one of {MODES}")
        self.chunks, self.mode, self.rrf_k = chunks, mode, rrf_k
        self.bm25 = BM25([tokenize(c.index_text) for c in chunks], k1=k1, b=b)
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform([content_text(c.index_text) for c in chunks])

    def _components(self, query: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        q_tokens = tokenize(query)
        bm25 = self.bm25.scores(q_tokens)
        max_bm25 = self.bm25.max_score(q_tokens)
        bm25_conf = np.clip(bm25 / max_bm25, 0, 1) if max_bm25 > 0 else np.zeros(len(self.chunks))
        q_vec = self.vectorizer.transform([content_text(query)])
        cosine = (self.matrix @ q_vec.T).toarray().ravel()  # rows are L2-normalised
        return bm25, bm25_conf, cosine

    def search(self, query: str, top_k: int = 3) -> RetrievalResult:
        bm25, bm25_conf, cosine = self._components(query)
        if self.mode == "bm25":
            order, native = np.argsort(-bm25, kind="stable"), bm25
            conf = bm25_conf
        elif self.mode == "tfidf":
            order, native = np.argsort(-cosine, kind="stable"), cosine
            conf = cosine
        else:
            fused = rrf([list(np.argsort(-bm25, kind="stable")), list(np.argsort(-cosine, kind="stable"))], self.rrf_k)
            order = np.array(sorted(fused, key=lambda i: (-fused[i], i)))
            native = np.array([fused[i] for i in range(len(self.chunks))])
            conf = (bm25_conf + cosine) / 2
        hits = [Hit(self.chunks[i], r, float(native[i]), float(bm25_conf[i]), float(cosine[i]))
                for r, i in enumerate(order[:top_k], start=1)]
        top_conf = float(conf[order[0]]) if hits and native[order[0]] > 0 else 0.0
        return RetrievalResult(self.mode, hits, top_conf)
