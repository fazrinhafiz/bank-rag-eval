"""Answer generators.

OfflineExtractive is a deterministic BASELINE, not an LLM: it copies the best-supported sentences from
the retrieved chunks and cites them. It exists so CI and the evaluation are reproducible, free and need
no API key - and so any real LLM has a floor to beat.

OpenAICompatible calls a chat-completions endpoint at temperature 0: OpenAI, Azure OpenAI, or any
OpenAI-compatible server (Ollama, vLLM) via a base_url. The `openai` package is an optional extra.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .ingest import Chunk, split_sentences
from .prompts import Prompt, estimate_tokens
from .text import tokenize

RANK_WEIGHT = {1: 1.0, 2: 0.8, 3: 0.6}


@dataclass
class Generation:
    text: str
    abstained: bool
    model: str
    prompt_tokens: int
    completion_tokens: int
    tokens_estimated: bool


class OfflineExtractive:
    provider = "offline"

    def __init__(self, prompt: Prompt, model: str = "offline-extractive-v1", max_sentences: int = 2):
        self.prompt, self.model, self.max_sentences = prompt, model, max_sentences

    def generate(self, question: str, chunks: list[Chunk]) -> Generation:
        q_terms = set(tokenize(question))
        candidates = []
        for rank, chunk in enumerate(chunks, start=1):
            for pos, sentence in enumerate(split_sentences(chunk.text)):
                overlap = len(q_terms & set(tokenize(sentence)))
                if overlap:
                    weight = RANK_WEIGHT.get(rank, 0.5)
                    candidates.append((overlap / max(len(q_terms), 1) * weight, rank, pos, sentence, chunk.id))
        prompt_tokens = estimate_tokens(" ".join(m["content"] for m in self.prompt.render(question, chunks)))
        if not candidates:
            text = self.prompt.abstain_text
            return Generation(text, True, self.model, prompt_tokens, estimate_tokens(text), True)
        best = sorted(candidates, key=lambda c: (-c[0], c[1], c[2]))[: self.max_sentences]
        best.sort(key=lambda c: (c[1], c[2]))  # keep document order for readability
        text = " ".join(f"{sentence} [{cid}]" for _, _, _, sentence, cid in best)
        return Generation(text, False, self.model, prompt_tokens, estimate_tokens(text), True)


class OpenAICompatible:
    """Env vars:
    OpenAI / compatible: OPENAI_API_KEY, optional OPENAI_BASE_URL (e.g. http://localhost:11434/v1 for Ollama)
    Azure OpenAI:        AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT,
                         optional AZURE_OPENAI_API_VERSION
    """

    def __init__(self, prompt: Prompt, model: str, max_tokens: int = 300, azure: bool = False, client=None):
        self.prompt, self.max_tokens = prompt, max_tokens
        self.provider = "azure" if azure else "openai"
        self.model = os.environ.get("AZURE_OPENAI_DEPLOYMENT", model) if azure else model
        self.client = client or self._make_client(azure)

    @staticmethod
    def _make_client(azure: bool):
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError("Install the optional extra: pip install -e '.[openai]'") from exc
        if azure:
            return openai.AzureOpenAI(
                azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                api_key=os.environ["AZURE_OPENAI_API_KEY"],
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            )
        return openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url=os.environ.get("OPENAI_BASE_URL"))

    def complete(self, messages: list[dict]) -> tuple[str, int | None, int | None]:
        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0, max_tokens=self.max_tokens)
        usage = getattr(resp, "usage", None)
        return (resp.choices[0].message.content or "").strip(), getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)

    def generate(self, question: str, chunks: list[Chunk]) -> Generation:
        messages = self.prompt.render(question, chunks)
        text, p_tok, c_tok = self.complete(messages)
        abstained = _is_abstain(text, self.prompt.abstain_text)
        estimated = p_tok is None or c_tok is None
        if estimated:
            p_tok = estimate_tokens(" ".join(m["content"] for m in messages))
            c_tok = estimate_tokens(text)
        return Generation(text, abstained, self.model, p_tok, c_tok, estimated)


def _is_abstain(text: str, abstain_text: str) -> bool:
    norm = lambda s: " ".join(s.lower().split())
    return norm(abstain_text)[:40] in norm(text)


def make_generator(cfg: dict, prompt: Prompt):
    gen = cfg["generation"]
    provider = gen["provider"]
    if provider == "offline":
        return OfflineExtractive(prompt, gen.get("model", "offline-extractive-v1"), gen.get("max_sentences", 2))
    if provider in ("openai", "azure"):
        model = gen["model"] if gen["model"] != "offline-extractive-v1" else "gpt-4o-mini"
        return OpenAICompatible(prompt, model, gen.get("max_tokens", 300), azure=provider == "azure")
    raise ValueError(f"unknown provider {provider!r}")
