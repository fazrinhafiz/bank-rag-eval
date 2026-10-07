"""Heading-aware chunking: one chunk per `##` section; long sections become overlapping windows."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


@dataclass(frozen=True)
class Chunk:
    id: str          # e.g. "credit-card#late-payment-charge"
    doc_id: str
    title: str
    section: str     # human-readable heading
    text: str        # section body only

    @property
    def index_text(self) -> str:
        """What the retrievers index: heading twice (cheap field boost) plus the body."""
        return f"{self.title}. {self.section}. {self.section}. {self.text}"


def slugify(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]


def _windows(sentences: list[str], max_words: int, overlap_words: int) -> list[str]:
    """Greedy sentence packing; each new window starts with the trailing ~overlap_words of the last one."""
    windows, current = [], []
    for sent in sentences:
        if current and sum(len(s.split()) for s in current) + len(sent.split()) > max_words:
            windows.append(" ".join(current))
            carry, words = [], 0
            for s in reversed(current):
                if words + len(s.split()) > overlap_words:
                    break
                carry.insert(0, s)
                words += len(s.split())
            current = carry
        current.append(sent)
    if current:
        windows.append(" ".join(current))
    return windows


def parse_document(markdown: str, max_words: int = 120, overlap_words: int = 30) -> list[Chunk]:
    m = _FRONT_MATTER.match(markdown)
    if not m:
        raise ValueError("document has no YAML front matter")
    meta = yaml.safe_load(m.group(1))
    doc_id, title = meta["doc_id"], meta.get("title", meta["doc_id"])
    body = markdown[m.end():]

    chunks: list[Chunk] = []
    for part in re.split(r"(?m)^## ", body)[1:]:  # text before the first ## (H1, disclaimer) is dropped
        heading, _, text = part.partition("\n")
        heading, text = heading.strip(), " ".join(text.split())
        if not text:
            continue
        slug = slugify(heading)
        pieces = _windows(split_sentences(text), max_words, overlap_words) if len(text.split()) > max_words else [text]
        for n, piece in enumerate(pieces, start=1):
            cid = f"{doc_id}#{slug}" if len(pieces) == 1 else f"{doc_id}#{slug}-{n}"
            chunks.append(Chunk(cid, doc_id, title, heading, piece))
    return chunks


def load_corpus(docs_dir: str | Path, max_words: int = 120, overlap_words: int = 30) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(Path(docs_dir).glob("*.md")):
        chunks.extend(parse_document(path.read_text(), max_words, overlap_words))
    ids = [c.id for c in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate chunk ids - two sections share a heading")
    return chunks
