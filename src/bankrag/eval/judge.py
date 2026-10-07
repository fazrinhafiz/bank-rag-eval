"""Optional LLM-as-judge for faithfulness. Only used when a real LLM is configured (`eval --judge`).

The judge sees one answer sentence and the retrieved passages and must reply SUPPORTED or UNSUPPORTED.
Judges have their own error rate: validate the judge against human labels on a sample before trusting
it, and keep the lexical faithfulness metric alongside it as a cheap cross-check.
"""
from __future__ import annotations

from .metrics import answer_sentences

JUDGE_SYSTEM = (
    "You are a strict fact-checker. Decide whether the CLAIM is fully supported by the PASSAGES. "
    "Numbers, limits and conditions must match exactly. Reply with exactly one word: SUPPORTED or UNSUPPORTED."
)


class LLMJudge:
    def __init__(self, completer):
        """completer: an object with .complete(messages) -> (text, prompt_tokens, completion_tokens)
        and a .model attribute - e.g. generators.OpenAICompatible."""
        self.completer = completer
        self.model = getattr(completer, "model", "unknown")

    def supported(self, claim: str, contexts: list[str]) -> bool:
        passages = "\n\n".join(f"PASSAGE {i}: {c}" for i, c in enumerate(contexts, start=1))
        text, _, _ = self.completer.complete([
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": f"{passages}\n\nCLAIM: {claim}"},
        ])
        return text.strip().upper().startswith("SUPPORTED")

    def faithfulness(self, answer: str, contexts: list[str]) -> float:
        sentences = answer_sentences(answer)
        if not sentences:
            return 0.0
        return sum(self.supported(s, contexts) for s in sentences) / len(sentences)
