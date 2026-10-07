"""Input and output guardrails. Deterministic, rule-based, and explainable: every decision has a reason code.

Input:  PII detection + masking (NRIC, Luhn-valid card numbers, Malaysian phone numbers, email) and
        prompt-injection heuristics.
Output: PII leak check and citation-present check.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Public bank contact details that legitimately appear in answers (the docs publish them).
ALLOWED_EMAIL_DOMAINS = ("seriandalas.example",)

_CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
_NRIC = re.compile(r"(?<![\d-])(\d{2})(\d{2})(\d{2})-?(\d{2})-?(\d{4})(?![\d-])")
# Mobile: 01X-XXX XXXX / +60 1X-XXXX XXXX. Landline: 03-XXXX XXXX. Bank hotlines (1-300-...) do not match.
_PHONE = re.compile(r"(?<![\w+])(?:\+?60[ -]?|0)(?:1\d|[3-9])[ -]?\d{3,4}[ -]?\d{4}(?!\d)")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_CITATION = re.compile(r"\[([a-z0-9-]+#[a-z0-9-]+)\]")

MASKS = {"card": "[CARD]", "nric": "[NRIC]", "phone": "[PHONE]", "email": "[EMAIL]"}


def luhn_valid(number: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _nric_plausible(m: re.Match) -> bool:
    month, day = int(m.group(2)), int(m.group(3))
    return 1 <= month <= 12 and 1 <= day <= 31


@dataclass(frozen=True)
class PIISpan:
    type: str
    start: int
    end: int
    value: str


def detect_pii(text: str) -> list[PIISpan]:
    """Non-overlapping PII spans, most specific detector first (card > NRIC > phone > email)."""
    spans: list[PIISpan] = []

    def free(s: int, e: int) -> bool:
        return all(e <= x.start or s >= x.end for x in spans)

    for m in _CARD.finditer(text):
        if luhn_valid(m.group()) and free(*m.span()):
            spans.append(PIISpan("card", *m.span(), m.group()))
    for m in _NRIC.finditer(text):
        if _nric_plausible(m) and free(*m.span()):
            spans.append(PIISpan("nric", *m.span(), m.group()))
    for m in _PHONE.finditer(text):
        if free(*m.span()):
            spans.append(PIISpan("phone", *m.span(), m.group()))
    for m in _EMAIL.finditer(text):
        domain = m.group().rsplit("@", 1)[1].lower()
        if not domain.endswith(ALLOWED_EMAIL_DOMAINS) and free(*m.span()):
            spans.append(PIISpan("email", *m.span(), m.group()))
    return sorted(spans, key=lambda s: s.start)


def mask_pii(text: str) -> tuple[str, list[PIISpan]]:
    spans = detect_pii(text)
    out = text
    for span in reversed(spans):
        out = out[: span.start] + MASKS[span.type] + out[span.end:]
    return out, spans


# --- prompt injection -------------------------------------------------------------------------------
# Each pattern is a named, reviewable rule. Kept deliberately specific to avoid blocking normal banking
# questions ("can my spouse act as guarantor?"); see tests/test_guardrails.py for the false-positive cases.
_INJECTION_RULES: dict[str, str] = {
    "override_instructions": r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instructions?|rules|prompt|guidelines|guardrails|documents|context|polic(y|ies))\b",
    "reveal_prompt": r"\b(reveal|show|print|repeat|display|output|leak|tell me)\b.{0,30}\b(system prompt|hidden (instructions?|prompt)|your (instructions|prompt|rules)|initial prompt)\b",
    "persona_switch": r"\b(you are now|from now on,? you|pretend (to be|you are|you're)|act as (an? )?(unrestricted|unfiltered|jailbroken|evil|different))\b",
    "roleplay": r"\b(role-?play|let'?s play a game|stay in character)\b",
    "jailbreak_terms": r"\b(DAN|do anything now|developer mode|jailbreak|no restrictions|unfiltered|without (any )?(restrictions|filters|limits))\b",
    "fake_system_turn": r"(^|\n)\s*(system|assistant|developer)\s*:|<\|?(im_start|system)\|?>|\[/?(inst|system)\]",
}
_COMPILED = {name: re.compile(p, re.I) for name, p in _INJECTION_RULES.items()}


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[​-‏⁠﻿]", "", text)  # zero-width characters
    return re.sub(r"[ \t]+", " ", text)


def detect_injection(text: str) -> list[str]:
    """Names of the injection rules the text triggers (empty list = clean)."""
    norm = _normalise(text)
    return [name for name, rx in _COMPILED.items() if rx.search(norm)]


@dataclass
class InputCheck:
    masked_text: str
    pii: list[PIISpan] = field(default_factory=list)
    injection_rules: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.injection_rules)


def check_input(text: str) -> InputCheck:
    masked, spans = mask_pii(text)
    return InputCheck(masked, spans, detect_injection(text))


# --- output ---------------------------------------------------------------------------------------------
def extract_citations(answer: str) -> list[str]:
    return _CITATION.findall(answer)


@dataclass
class OutputCheck:
    pii_leaks: list[str] = field(default_factory=list)   # PII types found in the answer
    citations: list[str] = field(default_factory=list)
    invalid_citations: list[str] = field(default_factory=list)

    @property
    def citation_ok(self) -> bool:
        return bool(self.citations) and not self.invalid_citations

    @property
    def leaked(self) -> bool:
        return bool(self.pii_leaks)


def check_output(answer: str, retrieved_ids: list[str], input_pii: list[PIISpan] = ()) -> OutputCheck:
    leaks = [s.type for s in detect_pii(answer)]
    digits_answer = re.sub(r"\D", "", answer)
    for span in input_pii:  # catch re-formatted echoes of the user's own PII, e.g. without dashes
        if span.value in answer or (sum(c.isdigit() for c in span.value) >= 10 and re.sub(r"\D", "", span.value) in digits_answer):
            leaks.append(span.type)
    cites = extract_citations(answer)
    return OutputCheck(sorted(set(leaks)), cites, [c for c in cites if c not in retrieved_ids])
