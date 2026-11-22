"""Name matching for Malaysian names, tolerant of the variations seen on real documents.

Handled: case, punctuation, patronymic particles (bin/binti/b./bt., a/l, a/p, s/o, d/o), common
abbreviations (Mohd/Muhd/Md -> Muhammad, Abd -> Abdul, Nor/Noor -> Nur) and word order (Tan Wei Ming vs
Wei Ming Tan). Comparison is on the set of remaining name tokens:
    identical sets            -> match
    Jaccard >= 0.5            -> partial   (e.g. a father's name dropped; a human should look)
    otherwise                 -> mismatch
"""
from __future__ import annotations

import re

PARTICLES = {"BIN", "BINTI", "BTE", "BT", "B", "AL", "AP", "SO", "DO"}
CANONICAL = {"MOHD": "MUHAMMAD", "MUHD": "MUHAMMAD", "MD": "MUHAMMAD", "MOHAMAD": "MUHAMMAD",
             "MOHAMMAD": "MUHAMMAD", "MOHAMED": "MUHAMMAD", "MOHAMMED": "MUHAMMAD", "MUHAMAD": "MUHAMMAD",
             "MUHAMMED": "MUHAMMAD", "ABD": "ABDUL", "NOR": "NUR", "NOOR": "NUR"}


def tokens(name: str | None) -> frozenset[str]:
    if not name:
        return frozenset()
    text = re.sub(r"\b([ASD])\s*/\s*([LPO])\b", r"\1\2", name.upper())   # A/L -> AL, S/O -> SO
    words = re.split(r"[^A-Z']+", text)
    return frozenset(CANONICAL.get(w, w) for w in words if w and w not in PARTICLES)


def compare(a: str | None, b: str | None) -> tuple[str, float]:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return "mismatch", 0.0
    score = len(ta & tb) / len(ta | tb)
    if ta == tb:
        return "match", 1.0
    return ("partial" if score >= 0.5 else "mismatch"), round(score, 3)
