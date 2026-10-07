"""Versioned prompt templates (prompts/*.yaml). The prompt version is recorded in every trace and report."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from .ingest import Chunk


@dataclass(frozen=True)
class Prompt:
    version: str
    system: str
    user: str
    abstain_text: str
    sha: str

    @classmethod
    def load(cls, path: str | Path) -> "Prompt":
        raw = Path(path).read_text()
        data = yaml.safe_load(raw)
        system = data["system"].format(abstain_text=data["abstain_text"])
        return cls(data["version"], system, data["user"], data["abstain_text"], hashlib.sha256(raw.encode()).hexdigest()[:12])

    def render(self, question: str, chunks: list[Chunk]) -> list[dict]:
        context = "\n\n".join(f"[{c.id}] ({c.title} - {c.section})\n{c.text}" for c in chunks)
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user.format(context=context, question=question)},
        ]


def estimate_tokens(text: str) -> int:
    """~4 characters per token for English. Used only when the provider does not report usage."""
    return max(1, round(len(text) / 4))
