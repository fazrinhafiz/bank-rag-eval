"""Tokenisation shared by retrieval, the extractive generator and the faithfulness metric."""
from __future__ import annotations

import re

STOPWORDS = frozenset("""
a an the is are was were be been being am i im i'm me my mine we our ours you your yours it its
of to in on at for with by from into onto about over under after before and or but nor so if as
than then too very also just only do does did doing done can could would should will shall may
might must what which who whom whose when where why how this that these those there here have has
had having any all some such each every much many more most other another own same up down out off
again further once please tell know want need get got let us he she they them their his her hi hello
""".split())

_TOKEN = re.compile(r"[a-z0-9]+(?:[.,]\d+)*%?")


def stem(word: str) -> str:
    """Tiny suffix stripper. Consistency matters more than linguistic accuracy: query and docs share it."""
    if len(word) <= 3 or any(ch.isdigit() for ch in word):
        return word
    for suffix, repl in (("ies", "y"), ("ied", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "s" and word.endswith(("ss", "us", "is")):
                break
            word = word[: -len(suffix)] + repl
            break
    if word.endswith("e") and len(word) >= 4:
        word = word[:-1]
    return word


def tokenize(text: str, drop_stopwords: bool = True) -> list[str]:
    tokens = _TOKEN.findall(text.lower())
    if drop_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return [stem(t) for t in tokens]


def content_text(text: str) -> str:
    """Stopword-free surface text, used as TF-IDF input."""
    return " ".join(t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS)
